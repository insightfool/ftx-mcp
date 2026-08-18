"""Schema-dump cache, cross-version diffing, and read-only accessors.

The value: OFFLINE schema access (the builtin UI type catalog + per-type
property schema, cached to disk keyed by Studio version) and CROSS-VERSION
diffing (what types/properties changed between two Studio releases — upgrade
intelligence). Once cached, every read here is pure-Python and needs no Studio
running.

The one live piece is the fetch: the C# design-time bridge must expose a
GET endpoint (SCHEMA_DUMP_ROUTE) that enumerates the whole type catalog x
`describe_type` reflection into the DUMP CONTRACT below and self-reports the
Studio version. Until that endpoint ships, fetch_schema_dump ALWAYS raises
BridgeUnavailable on a real box — that is expected; this module is the Python
half wired and tested ahead of the endpoint, exercised with synthetic dumps.

DUMP CONTRACT (mirrors the bridge's existing describe_type reflection, which
returns per-property name/datatype/settable):

    {
      "studio_version": "1.7.1.46",
      "generated_at": "<iso8601>",
      "types": {
        "<TypeName>": {
          "browse_name": "<str>",
          "properties": [
            {"name": "...", "datatype": "...", "settable": true},
            ...
          ]
        },
        ...
      }
    }

The dump self-reports `studio_version`; callers never read the version
separately. Cache files are named `<sanitized-version>.json` under
`cfg.state_dir / "schema"`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from . import core

# The bridge GET endpoint that PRODUCES the dump. VM-only (C# bridge); not yet
# shipped. Until it exists, fetch_schema_dump raises BridgeUnavailable.
SCHEMA_DUMP_ROUTE = "/bridge/schema/dump"


def fetch_schema_dump(
    cfg: core.Config,
    project: str,
    get_json: Callable[[core.Config, str], tuple[int, dict]] | None = None,
) -> dict:
    """Fetch the schema dump from the design-time bridge.

    Mirrors how core.list_ui_types / core.describe_type call the bridge: guard
    that the bridge is serving `project`, then GET the endpoint via core's
    JSON helper. `get_json` is a test-injection seam — a callable
    (cfg, path) -> (status, data); it defaults to core._bridge_get_json (the
    real bridge call). Propagates BridgeUnavailable when the bridge is down or
    the endpoint is absent (the expected state until the C# endpoint lands).
    """
    if get_json is None:
        get_json = core._bridge_get_json
        # AInsightfool: rebind to the SPECIFIC bridge serving `project` (one of
        # possibly several simultaneously armed, each on its own port) rather
        # than the old implicit single cfg.bridge_url.
        cfg = core._require_bridge_for(cfg, project)
    status, data = get_json(cfg, SCHEMA_DUMP_ROUTE)
    if status != 200 or "types" not in data or "studio_version" not in data:
        raise core.BridgeUnavailable(
            f"bridge {SCHEMA_DUMP_ROUTE} returned status={status} "
            "(schema-dump endpoint not available yet — needs the bridge build)"
        )
    return data


def schema_cache_dir(cfg: core.Config) -> Path:
    """The on-disk schema cache dir (`cfg.state_dir / "schema"`), created."""
    d = cfg.state_dir / "schema"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_key(v: str) -> str:
    """Sanitize a version string into a safe filename stem.

    Strips surrounding whitespace and maps any char that is not
    alphanumeric / `.` / `-` to `_`. Keeps `1.7.1.46` intact; neutralizes
    path separators and other surprises.
    """
    return re.sub(r"[^A-Za-z0-9.\-]", "_", (v or "").strip())


def cache_path(cfg: core.Config, studio_version: str) -> Path:
    """The on-disk path a dump of `studio_version` caches to (may not exist)."""
    return schema_cache_dir(cfg) / f"{_version_key(studio_version)}.json"


def cache_dump(cfg: core.Config, dump: dict) -> Path:
    """Write `dump` to schema_cache_dir/<version-key>.json; return the path."""
    path = cache_path(cfg, dump.get("studio_version", ""))
    path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    return path


def load_dump(cfg: core.Config, studio_version: str) -> dict | None:
    """Read a cached dump back by version; None if absent or unparseable."""
    path = cache_path(cfg, studio_version)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_cached(cfg: core.Config) -> list[str]:
    """The studio_version strings for every cached dump, sorted.

    Reads each file's self-reported `studio_version`; falls back to the
    filename stem for a file that cannot be parsed.
    """
    d = schema_cache_dir(cfg)
    versions: set[str] = set()
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            v = data.get("studio_version") if isinstance(data, dict) else None
        except (ValueError, OSError, UnicodeDecodeError):
            v = None
        versions.add(v if isinstance(v, str) and v else f.stem)
    return sorted(versions)


def ensure_dump(
    cfg: core.Config,
    project: str,
    refresh: bool = False,
    get_json: Callable[[core.Config, str], tuple[int, dict]] | None = None,
) -> dict:
    """Fetch a fresh dump from the bridge, cache it, and return it.

    `refresh` is moot server-side — the dump is always generated fresh by the
    bridge on each call; the param is kept for call-site symmetry with other
    cache-backed accessors. Propagates BridgeUnavailable from the fetch.
    """
    dump = fetch_schema_dump(cfg, project, get_json=get_json)
    cache_dump(cfg, dump)
    return dump


def _prop_index(props: Any) -> dict[str, dict]:
    """Index a type's property list by name (last-wins on dup names)."""
    out: dict[str, dict] = {}
    if isinstance(props, list):
        for p in props:
            if isinstance(p, dict) and isinstance(p.get("name"), str):
                out[p["name"]] = p
    return out


def _prop_shape(p: dict) -> dict:
    """The comparable shape of a property: its datatype + settable flag."""
    return {"datatype": p.get("datatype"), "settable": p.get("settable")}


def schema_diff(a: dict, b: dict) -> dict:
    """Pure diff of two dumps (a = older/from, b = newer/to).

    Returns:
      {"added_types":   [names present in b not a],
       "removed_types": [names present in a not b],
       "changed_types": {name: {"added_props":   [names in b not a],
                                "removed_props": [names in a not b],
                                "changed_props": [{"name", "from", "to"}]}}}

    A property is "changed" when the same name has a different datatype OR a
    different settable flag; `from`/`to` each carry {datatype, settable}. All
    orderings are sorted for determinism; a type with no property delta is
    omitted from changed_types.
    """
    a_types = a.get("types") or {}
    b_types = b.get("types") or {}
    a_names = set(a_types)
    b_names = set(b_types)

    added_types = sorted(b_names - a_names)
    removed_types = sorted(a_names - b_names)

    changed_types: dict[str, dict] = {}
    for name in sorted(a_names & b_names):
        a_props = _prop_index((a_types.get(name) or {}).get("properties"))
        b_props = _prop_index((b_types.get(name) or {}).get("properties"))
        added_props = sorted(set(b_props) - set(a_props))
        removed_props = sorted(set(a_props) - set(b_props))
        changed_props = []
        for pname in sorted(set(a_props) & set(b_props)):
            before = _prop_shape(a_props[pname])
            after = _prop_shape(b_props[pname])
            if before != after:
                changed_props.append({"name": pname, "from": before, "to": after})
        if added_props or removed_props or changed_props:
            changed_types[name] = {
                "added_props": added_props,
                "removed_props": removed_props,
                "changed_props": changed_props,
            }

    return {
        "added_types": added_types,
        "removed_types": removed_types,
        "changed_types": changed_types,
    }


def dump_summary(dump: dict, path: Path | None = None) -> dict:
    """A compact, size-safe summary of a dump (never the full type payload).

    Returns {studio_version, generated_at, type_count, property_count,
    path}. `property_count` sums the property lists across all types.
    """
    types = dump.get("types") or {}
    prop_count = 0
    for t in types.values():
        props = t.get("properties") if isinstance(t, dict) else None
        if isinstance(props, list):
            prop_count += len(props)
    return {
        "studio_version": dump.get("studio_version"),
        "generated_at": dump.get("generated_at"),
        "type_count": len(types),
        "property_count": prop_count,
        "path": str(path) if path is not None else None,
    }
