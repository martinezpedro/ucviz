"""Per-viewer catalog/schema scoping — v2 extension point (NOT active in v1).

v1 behaviour: every viewer sees every catalog/schema the query identity can see
(optionally narrowed by the admin ``catalogs`` list). These functions are the
single choke point the pickers call, so v2 can plug in here without touching
the UI code.

v2 design (see README, "v2 — per-group catalog scoping"):

* A UC mapping table, e.g. ``<admin_catalog>.ucviz.access_mapping``::

      group_name STRING,   -- Entra ID / account group
      catalog    STRING,   -- catalog the group may explore in UCViz
      schema     STRING    -- NULL = every schema in that catalog

* ``visible_catalogs`` resolves the viewer's groups (``principals.user_groups``),
  reads the rows for those groups once per session (cached), and returns the
  intersection with ``catalogs``. ``visible_schemas`` does the same per catalog.
* A viewer with no mapped rows gets an empty list; the page then renders
  "You don't have access" instead of the pickers (see ``no_access_message``).
* Config (``config/app_config.yaml``)::

      access_scope:
        enabled: false
        mapping_table: <admin_catalog>.ucviz.access_mapping

  Only meaningful with ``query_identity: app`` — under on-behalf-of-user auth
  UC itself already limits what each viewer can see.
"""

from __future__ import annotations


def scope_enabled(cfg: dict) -> bool:
    """True once v2 scoping is switched on in config (always False in v1)."""
    return False


def visible_catalogs(cfg: dict, viewer: str, catalogs: list[str]) -> list[str]:
    """Catalogs ``viewer`` may explore. v1: passthrough."""
    return list(catalogs)


def visible_schemas(cfg: dict, viewer: str, catalog: str,
                    schemas: list[str]) -> list[str]:
    """Schemas of ``catalog`` that ``viewer`` may explore. v1: passthrough."""
    return list(schemas)


def no_access_message() -> str:
    return ("You don't have access to any catalogs in UCViz. Ask a UCViz admin "
            "to map one of your groups to a catalog.")
