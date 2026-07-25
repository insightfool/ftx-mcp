"""Offline, Studio-CLOSED structural validator for an Optix project tree (U17).

Tier-0 checks that need no bridge, no running Studio, and no Windows: they read
the `Nodes/**/*.yaml` source tree on disk and flag the mechanical mistakes that
otherwise only surface as a red export or a silently-transparent node at
runtime. The point is a fast pre-flight the author can run on Linux/CI before
paying for a real Studio export.

WHAT THIS CATCHES (hard ERRORs — a project with any of these will not behave):

  * YAML that does not parse (`yaml.safe_load` raises). A malformed node file is
    the single most common self-inflicted break.
  * `Id:` values that are not Optix GUIDs (`^g=[0-9a-f]{32}$`).
  * DUPLICATE `Id:` values within the project — Optix identity is the GUID; two
    nodes sharing one is corruption.

WHAT THIS CATCHES (WARN only, and ONLY with a --schema dump):

  * Type/property membership: a node whose `Type:` is a known type in the
    supplied U15 schema dump, carrying a property key that the dump does not
    list for that type. This is WARN, never ERROR, on purpose — the
    reflection-derived schema is INCOMPLETE (see honest-scope note below), so a
    validator that REJECTED unknown properties would reject things Studio
    happily accepts, which is net-negative. Without a schema dump this tier is
    skipped entirely; the structural ERRORs still run.

HONEST SCOPE — this "catches most errors", it is NOT a proof of validity. The
schema half is derived from bridge reflection over loaded types, which does not
see editor-logic validity: NetLogic wiring, dynamic-link targets, converter
placement, ExpressionEvaluator syntax, and inherited/aliased properties are all
invisible here. A clean report means "no MECHANICAL/structural fault found",
not "this project is correct". Treat a real Studio export (the --oracle path in
service/_validate_cli.py) as the ground truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Optix node identity: the literal `g=` prefix + 32 lowercase hex chars.
_GUID_RE = re.compile(r"^g=[0-9a-f]{32}$")

# Block-form `Id:` key at the start of a (possibly list-item) line. We ONLY
# validate declaration-position ids: `  Id: g=...` or `  - Id: g=...`. Inline
# flow-map id *references* (NodeId pointers embedded mid-line) legitimately
# repeat a declared id, so scanning them would produce false duplicate ERRORs.
_ID_LINE_RE = re.compile(r"^\s*(?:- )?Id:\s*['\"]?(?P<val>[^'\"\s]+)['\"]?\s*$")

# Keys that are structural node machinery, never a settable "property" — they
# are excluded from the schema-membership WARN check.
_STRUCTURAL_KEYS = frozenset({"Name", "Type", "Children", "Id"})


@dataclass
class _Report:
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def error(self, file: str, code: str, detail: str, line: int | None = None) -> None:
        item: dict[str, Any] = {"file": file, "code": code, "detail": detail}
        if line is not None:
            item["line"] = line
        self.errors.append(item)

    def warn(self, file: str, code: str, detail: str, line: int | None = None) -> None:
        item: dict[str, Any] = {"file": file, "code": code, "detail": detail}
        if line is not None:
            item["line"] = line
        self.warnings.append(item)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.errors,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _iter_yaml_files(project_dir: Path) -> list[Path]:
    """Every `*.yaml` under the project tree, sorted for stable reporting.

    Optix keeps node YAML under `Nodes/`, but projects differ and fixtures may
    not; a full recursive sweep of the tree is the robust choice and still only
    ever touches `*.yaml`.
    """
    return sorted(project_dir.rglob("*.yaml"))


def _rel(project_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def _walk_type_nodes(obj: Any):
    """Yield every mapping in a parsed YAML doc that declares a `Type:`.

    Recurses through dicts and lists so nodes at any nesting depth (root
    mapping + arbitrarily deep `Children:` lists) are all visited.
    """
    if isinstance(obj, dict):
        if isinstance(obj.get("Type"), str):
            yield obj
        for value in obj.values():
            yield from _walk_type_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_type_nodes(item)


def _schema_property_index(schema: dict[str, Any]) -> dict[str, set[str]]:
    """Map `TypeName -> {property name, ...}` from a U15 schema dump.

    Dump shape (service/optix_schema.py DUMP CONTRACT):
      {studio_version, generated_at, types: {<T>: {browse_name, properties:[
        {name, datatype, settable}, ...]}}}
    """
    out: dict[str, set[str]] = {}
    for type_name, spec in (schema.get("types") or {}).items():
        props = spec.get("properties") if isinstance(spec, dict) else None
        names: set[str] = set()
        if isinstance(props, list):
            for prop in props:
                if isinstance(prop, dict) and isinstance(prop.get("name"), str):
                    names.add(prop["name"])
        out[str(type_name)] = names
    return out


def validate_project(project_dir: Path, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run Tier-0 structural checks over `project_dir`, Studio-CLOSED.

    Returns a structured report: {ok, errors:[{file,line?,code,detail}],
    warnings:[...]}. `ok` is True iff there are zero errors. Pass `schema` (a
    parsed U15 dump) to additionally run the WARN-only membership tier.
    """
    project_dir = Path(project_dir)
    report = _Report()

    if not project_dir.is_dir():
        report.error(str(project_dir), "project_missing", f"not a directory: {project_dir}")
        return report.as_dict()

    prop_index = _schema_property_index(schema) if schema else None

    # (guid value) -> (relfile, line) of its FIRST declaration, for dup detection.
    seen_ids: dict[str, tuple[str, int]] = {}

    for path in _iter_yaml_files(project_dir):
        rel = _rel(project_dir, path)
        raw = path.read_text(encoding="utf-8", errors="replace")

        # 1. Parse. A failure is a hard ERROR and we skip the deeper tiers for
        #    this file (a doc that won't parse has no reliable structure to
        #    walk), but the line-based Id scan below still runs against the raw
        #    text so a broken file's obvious bad GUIDs are still surfaced.
        parsed: Any = None
        parse_ok = False
        try:
            parsed = yaml.safe_load(raw)
            parse_ok = True
        except yaml.YAMLError as exc:
            line = None
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                line = mark.line + 1  # PyYAML marks are 0-based
            report.error(rel, "yaml_parse", f"YAML parse failure: {exc}", line=line)

        # 2. GUID format + duplicate detection via a line scan (gives line
        #    numbers a parsed walk cannot). Declaration-position `Id:` only.
        for lineno, text in enumerate(raw.splitlines(), start=1):
            m = _ID_LINE_RE.match(text)
            if not m:
                continue
            val = m.group("val")
            if not _GUID_RE.match(val):
                report.error(
                    rel, "guid_format",
                    f"Id value is not a valid Optix GUID (expected g=<32 hex>): {val!r}",
                    line=lineno,
                )
                continue
            if val in seen_ids:
                first_file, first_line = seen_ids[val]
                report.error(
                    rel, "guid_duplicate",
                    f"duplicate Id {val} (first seen {first_file}:{first_line})",
                    line=lineno,
                )
            else:
                seen_ids[val] = (rel, lineno)

        # 3. Type/property membership — WARN only, only with a schema dump, only
        #    on parsed docs.
        if parse_ok and prop_index is not None:
            for node in _walk_type_nodes(parsed):
                type_name = node["Type"]
                known = prop_index.get(type_name)
                if known is None:
                    # Type not in the (incomplete) dump — cannot judge its
                    # properties, so stay silent rather than warn.
                    continue
                for key in node:
                    if key in _STRUCTURAL_KEYS or key in known:
                        continue
                    report.warn(
                        rel, "unknown_property",
                        f"property {key!r} not listed for type {type_name!r} in schema "
                        f"(schema is reflection-derived and incomplete — verify manually)",
                    )

    return report.as_dict()
