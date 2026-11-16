"""External metadata integration (generic).

UCViz does not ship any customer's source systems. Instead it reads whatever
External Metadata objects and external-lineage edges the customer has already
registered, and offers thin helpers to register new ones from the Admin panel.

The External Metadata / External Lineage SDK surfaces are relatively new, so
every call probes for the available attribute names and degrades gracefully.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient


def _external_metadata_api(client: WorkspaceClient):
    for attr in ("external_metadata", "external_metadatas"):
        api = getattr(client, attr, None)
        if api is not None:
            return api
    return None


def _external_lineage_api(client: WorkspaceClient):
    for attr in ("external_lineage", "lineage"):
        api = getattr(client, attr, None)
        if api is not None:
            return api
    return None


def list_external_sources() -> list[dict]:
    """Return registered External Metadata objects as plain dicts."""
    client = WorkspaceClient()
    api = _external_metadata_api(client)
    if api is None:
        return []
    out = []
    try:
        for obj in api.list():
            out.append(
                {
                    "name": getattr(obj, "name", None),
                    "system_type": getattr(obj, "system_type", None),
                    "description": getattr(obj, "description", None),
                    "url": getattr(obj, "url", None),
                }
            )
    except Exception:
        return []
    return out


def register_external_source(
    name: str, system_type: str, description: str = "", url: str | None = None
) -> dict:
    """Register a single External Metadata object. Returns a status dict."""
    client = WorkspaceClient()
    api = _external_metadata_api(client)
    if api is None:
        return {"name": name, "status": "skipped",
                "detail": "External Metadata API unavailable in this SDK."}
    try:
        api.create(
            name=name,
            system_type=system_type or "OTHER",
            entity_type="EXTERNAL_SOURCE",
            description=description,
            url=url,
        )
        return {"name": name, "status": "created", "detail": ""}
    except Exception as exc:
        msg = str(exc)
        return {
            "name": name,
            "status": "exists" if "exist" in msg.lower() else "error",
            "detail": msg,
        }


def link_source_to_table(source_name: str, target_table: str) -> dict:
    """Create one external-lineage edge source -> target table."""
    client = WorkspaceClient()
    api = _external_lineage_api(client)
    if api is None:
        return {"source": source_name, "target": target_table,
                "status": "skipped",
                "detail": "External Lineage API unavailable in this SDK."}
    try:
        api.create(
            source={"external_metadata": {"name": source_name}},
            target={"table": {"name": target_table}},
        )
        return {"source": source_name, "target": target_table,
                "status": "linked", "detail": ""}
    except Exception as exc:
        msg = str(exc)
        return {
            "source": source_name,
            "target": target_table,
            "status": "exists" if "exist" in msg.lower() else "error",
            "detail": msg,
        }
