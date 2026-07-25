"""CLI for the offline Optix validator + the Studio-export oracle (U17).

Mirrors service/_token_admin.py conventions: own `build_parser()` + `main(argv)`,
invoked as `python -m service._validate_cli` (or the `ftx-mcp-validate` console
script). Text-first output for humans; `--json` for machine consumers.

    validate <project-dir> [--schema <dump.json>] [--oracle] [--json]

The default path is Studio-CLOSED and Linux-buildable (see optix_validate). The
`--oracle` flag adds a SECOND, Windows-only ground-truth check: it invokes the
real Studio `export` verb against a throwaway staging dir and reports what
Studio actually did. The oracle is a DORMANT, standalone code path — it never
calls core.deploy()/deploy_updatesvc and never flips Config.enable_deploy; it
just runs the same argv shape core.deploy would, read-only-ish (see the
windows_validation caveat in the handoff: whether `export` mutates the SOURCE
tree is an open question — run it against a clean/committed tree).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import optix_validate

# Same platform gate core uses for its Windows-only surfaces.
_IS_WINDOWS = os.name == "nt"

# Studio export can be slow; keep a generous but bounded ceiling so a hung
# Studio child cannot wedge the CLI forever.
_ORACLE_TIMEOUT_SECONDS = 300


def _load_schema(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "types" not in data:
        raise ValueError(f"schema dump {path} missing top-level 'types'")
    return data


def _resolve_studio_exe() -> Path | None:
    """Studio exe path via the SAME resolution core.Config uses, or None.

    Imported lazily so the structural validator (and its tests) never pay the
    cost of importing core. On Linux this resolves to a path that does not
    exist, which the oracle reports as unavailable.
    """
    try:
        from . import core
    except Exception:
        return None
    try:
        return core.Config.from_env().studio_exe
    except Exception:
        return None


def run_export_oracle(project_dir: Path, studio_exe: Path | None) -> dict[str, Any]:
    """Ground-truth check: run Studio's real `export` verb, report the outcome.

    Standalone — does NOT call core.deploy/deploy_updatesvc and does NOT touch
    Config.enable_deploy. Resolves the `.optix` via sorted(glob("*.optix"))[0],
    runs the SAME argv shape core.deploy uses:

        [studio_exe, "export", <optix>, "--platform=Win32_x64", "--location=<tmp>"]

    into a throwaway `tempfile.mkdtemp()` dir (cleaned up afterwards), and parses
    returncode/stderr into {returncode, stdout_tail, stderr_tail, staged_files}.
    An export that exits 0 but stages ZERO files is itself a finding.

    Studio-required: on non-Windows or a missing studio_exe it returns a clean
    {"oracle": "oracle_unavailable", ...} note and never raises.
    """
    project_dir = Path(project_dir)

    if not _IS_WINDOWS:
        return {"oracle": "oracle_unavailable", "reason": f"non-Windows host ({os.name})"}
    if studio_exe is None or not Path(studio_exe).is_file():
        return {"oracle": "oracle_unavailable", "reason": f"studio_exe not found: {studio_exe}"}

    optix_files = sorted(glob.glob(str(project_dir / "*.optix")))
    if not optix_files:
        return {"oracle": "oracle_unavailable", "reason": f"no .optix under {project_dir}"}
    optix_file = optix_files[0]

    staging_dir = tempfile.mkdtemp(prefix="ftx-oracle-")
    try:
        cmd = [
            str(studio_exe),
            "export",
            optix_file,
            "--platform=Win32_x64",
            f"--location={staging_dir}",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_ORACLE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "oracle": "timeout",
                "reason": f"export exceeded {_ORACLE_TIMEOUT_SECONDS}s",
                "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
            }

        staged_files = sorted(
            str(Path(p).relative_to(staging_dir))
            for p in glob.glob(os.path.join(staging_dir, "**", "*"), recursive=True)
            if os.path.isfile(p)
        )
        result: dict[str, Any] = {
            "oracle": "ran",
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
            "staged_files": staged_files,
            "optix_file": os.path.basename(optix_file),
        }
        # An exit-0 export that staged nothing is a silent failure worth flagging.
        if proc.returncode == 0 and not staged_files:
            result["finding"] = "export exited 0 but staged 0 files"
        return result
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _print_human(report: dict[str, Any], oracle: dict[str, Any] | None) -> None:
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    for err in errors:
        loc = err["file"] + (f":{err['line']}" if err.get("line") is not None else "")
        print(f"ERROR  {loc}  [{err['code']}] {err['detail']}")
    for warn in warnings:
        loc = warn["file"] + (f":{warn['line']}" if warn.get("line") is not None else "")
        print(f"WARN   {loc}  [{warn['code']}] {warn['detail']}")
    status = "OK" if report.get("ok") else "FAIL"
    print(f"{status}: {len(errors)} error(s), {len(warnings)} warning(s)")
    if oracle is not None:
        state = oracle.get("oracle")
        if state == "oracle_unavailable":
            print(f"oracle: unavailable ({oracle.get('reason')})")
        elif state == "ran":
            note = f", {oracle['finding']}" if oracle.get("finding") else ""
            print(
                f"oracle: export returncode={oracle['returncode']}, "
                f"{len(oracle['staged_files'])} staged file(s){note}"
            )
        else:
            print(f"oracle: {state} ({oracle.get('reason', '')})")


def cmd_validate(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir)

    schema: dict[str, Any] | None = None
    if args.schema:
        schema = _load_schema(args.schema)

    report = optix_validate.validate_project(project_dir, schema=schema)

    oracle: dict[str, Any] | None = None
    if args.oracle:
        oracle = run_export_oracle(project_dir, _resolve_studio_exe())

    if args.json:
        out: dict[str, Any] = dict(report)
        if oracle is not None:
            out["oracle"] = oracle
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_human(report, oracle)

    return 0 if report.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m service._validate_cli",
        description="Offline Optix project validator (structural, Studio-closed) "
                    "with an optional Windows-only Studio-export oracle.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate", help="validate a project tree")
    p_val.add_argument("project_dir", help="path to the Optix project directory")
    p_val.add_argument(
        "--schema", default=None,
        help="U15 schema dump JSON; enables the WARN-only type/property membership tier",
    )
    p_val.add_argument(
        "--oracle", action="store_true",
        help="ALSO run the real Studio export as ground truth (Windows + Studio required)",
    )
    p_val.add_argument(
        "--json", action="store_true",
        help="emit the structured report as JSON instead of human text",
    )
    p_val.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        return 1
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
