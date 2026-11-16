"""Real-time lineage via the Unity Catalog lineage-tracking REST API.

``system.access.table_lineage`` (a system *table*) can lag table creation by up
to an hour, which makes it useless for "I just built this data product, show me
its lineage". The lineage-tracking REST API — the same source Catalog Explorer
uses — is near real-time, so UCViz reads from it instead.

We call the REST endpoints directly (not through the SDK's WorkspaceClient) to
control auth precisely: in a Databricks App the environment carries the service
principal's OAuth credentials, so handing the SDK a *viewer* token on top of
that trips its "more than one authorization method configured" guard. Here we
send exactly one Authorization header — the service principal's.

Both directions are supported: ``get_upstreams`` (source side, for tracing a
product back to origin) and ``get_downstreams`` (consumer side, for finding
every table built *from* a given one).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from modules.db import _cfg

TABLE_LINEAGE_PATH = "/api/2.0/lineage-tracking/table-lineage"
EXTERNAL_LINEAGE_PATH = "/api/2.0/lineage-tracking/external-lineage"

KIND_TABLE = "TABLE"
KIND_EXTERNAL = "EXTERNAL"
KIND_PATH = "PATH"

DIR_UPSTREAM = "UPSTREAM"
DIR_DOWNSTREAM = "DOWNSTREAM"


def _auth_headers() -> dict:
    """Authenticate as the service principal (full API scope).

    The lineage-tracking REST API is not covered by the on-behalf-of-user
    ``sql`` scope, so the viewer token cannot call it. The app service principal
    has full API scope; it is granted read access to the demo schemas so it can
    resolve their lineage (including declarative-pipeline streaming tables /
    materialized views, which the system table does not record).
    """
    try:
        # Config.authenticate() returns the SP's {'Authorization': 'Bearer ...'}.
        return dict(_cfg.authenticate() or {})
    except Exception:
        return {}


def _request(method: str, path: str, query: dict | None = None,
             body: dict | None = None) -> dict:
    url = _cfg.host.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(_auth_headers())
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _parse_lineage_item(item: dict) -> dict | None:
    """Normalize a table-lineage entry into ``{name, kind, system}``.

    Works for both upstream and downstream entries (same schema either way).
    """
    if not isinstance(item, dict):
        return None
    if "tableInfo" in item:
        t = item["tableInfo"]
        cat, sch, name = (t.get("catalog_name"), t.get("schema_name"),
                          t.get("name"))
        if cat and sch and name:
            return {"name": f"{cat}.{sch}.{name}", "kind": KIND_TABLE,
                    "system": None}
    if "fileInfo" in item and isinstance(item["fileInfo"], dict):
        path = (item["fileInfo"].get("path")
                or item["fileInfo"].get("storage_location"))
        if path:
            return {"name": str(path), "kind": KIND_PATH, "system": None}
    return None


def _external_lineage(table_full_name: str, direction: str) -> list[dict]:
    """Registered external-system neighbors (e.g. SAP, Salesforce) for a table.

    ``direction`` is ``UPSTREAM`` (origins) or ``DOWNSTREAM`` (external sinks).
    """
    resp = _request(
        "GET", EXTERNAL_LINEAGE_PATH,
        query={"lineage_direction": direction,
               "object_info.table.name": table_full_name},
    )
    out: list[dict] = []
    for rel in resp.get("external_lineage_relationships", []) or []:
        info = rel.get("external_metadata_info") or {}
        if info.get("name"):
            out.append({"name": str(info["name"]), "kind": KIND_EXTERNAL,
                        "system": info.get("system_type")})
        elif rel.get("file_info"):
            path = rel["file_info"].get("path")
            if path:
                out.append({"name": str(path), "kind": KIND_PATH,
                            "system": None})
    return out


def _table_lineage(table_full_name: str, key: str) -> list[dict]:
    """Return normalized entries from the ``upstreams``/``downstreams`` array."""
    resp = _request(
        "GET", TABLE_LINEAGE_PATH,
        body={"table_name": table_full_name, "include_entity_lineage": True},
    )
    out: list[dict] = []
    seen: set[str] = set()
    for item in resp.get(key, []) or []:
        parsed = _parse_lineage_item(item)
        if parsed and parsed["name"] not in seen:
            seen.add(parsed["name"])
            out.append(parsed)
    return out


def get_upstreams(table_full_name: str) -> list[dict]:
    """Return direct upstream nodes (tables + external origins) for a table."""
    out = _table_lineage(table_full_name, "upstreams")
    seen = {n["name"] for n in out}
    for ext in _external_lineage(table_full_name, DIR_UPSTREAM):
        if ext["name"] not in seen:
            seen.add(ext["name"])
            out.append(ext)
    return out


def get_downstreams(table_full_name: str) -> list[dict]:
    """Return direct downstream nodes (tables + external sinks) for a table."""
    out = _table_lineage(table_full_name, "downstreams")
    seen = {n["name"] for n in out}
    for ext in _external_lineage(table_full_name, DIR_DOWNSTREAM):
        if ext["name"] not in seen:
            seen.add(ext["name"])
            out.append(ext)
    return out
