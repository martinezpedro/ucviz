"""Permission-chain resolver over ``information_schema`` privilege views.

For each table in the lineage chain we read **effective** access, not just the
grants defined directly on the table:

* ``information_schema.table_privileges`` already surfaces grants inherited from
  the schema and catalog — its ``inherited_from`` column is ``NONE`` (direct),
  ``SCHEMA`` or ``CATALOG`` — so one query per schema captures direct **and**
  inherited grants.
* table / schema / catalog **owners** hold every privilege implicitly, so each
  owner is added as an ``OWNER``-sourced access row (owners never appear in the
  privilege views).

Tables with no effective access at all are flagged as gaps; grants held by an
unusually broad principal are flagged as potential over-permissioning, and the
message notes whether that broad grant is direct or inherited.
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from modules.db import run_query
from modules.lineage_traversal import classify_layer
from modules.principals import classify_principal

STATUS_GRANTED = "granted"
STATUS_MISSING = "missing"
STATUS_UNEXPECTED = "unexpected"

# Where an effective grant comes from.
SOURCE_DIRECT = "DIRECT"
SOURCE_SCHEMA = "SCHEMA"
SOURCE_CATALOG = "CATALOG"
SOURCE_OWNER = "OWNER"

# Maps table_privileges.inherited_from -> our source label.
_INHERITED_MAP = {"NONE": SOURCE_DIRECT, "SCHEMA": SOURCE_SCHEMA,
                  "CATALOG": SOURCE_CATALOG}

# Principals whose SELECT grant on a governed table is worth flagging.
_BROAD_PRINCIPALS = {"account users", "users", "public"}

_PERM_COLUMNS = ["table", "layer", "grantor", "grantee", "privilege",
                 "is_grantable", "source", "principal_type", "status",
                 "has_access"]


def split_name(full_name: str):
    """Split ``catalog.schema.table``; return (catalog, schema, table)."""
    parts = (full_name or "").split(".")
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


# --------------------------------------------------------------------------- #
# Fetchers (one query per scope, matching the batched pattern elsewhere)
# --------------------------------------------------------------------------- #
def _fetch_schema_privileges(catalog, schema) -> pd.DataFrame:
    """All table grants in one schema — direct *and* inherited.

    ``inherited_from`` distinguishes DIRECT / SCHEMA / CATALOG grants.
    """
    q = f"""
        SELECT table_name, grantor, grantee, privilege_type, is_grantable,
               inherited_from
        FROM `{catalog}`.information_schema.table_privileges
        WHERE table_schema = '{schema}'
    """
    try:
        return run_query(q)
    except Exception:
        return pd.DataFrame(
            columns=["table_name", "grantor", "grantee", "privilege_type",
                     "is_grantable", "inherited_from"]
        )


def _fetch_table_meta(catalog, schema) -> pd.DataFrame:
    """Owner + comment for every table in one schema."""
    q = f"""
        SELECT table_name, table_owner, comment
        FROM `{catalog}`.information_schema.tables
        WHERE table_schema = '{schema}'
    """
    try:
        return run_query(q)
    except Exception:
        return pd.DataFrame(columns=["table_name", "table_owner", "comment"])


def _fetch_schema_owner(catalog, schema) -> str | None:
    try:
        df = run_query(
            f"SELECT schema_owner FROM `{catalog}`.information_schema.schemata "
            f"WHERE schema_name = '{schema}'"
        )
        if not df.empty:
            return df["schema_owner"].iloc[0]
    except Exception:
        pass
    return None


def _fetch_catalog_owner(catalog) -> str | None:
    try:
        df = run_query(
            "SELECT catalog_owner FROM "
            f"`{catalog}`.information_schema.catalogs "
            f"WHERE catalog_name = '{catalog}'"
        )
        if not df.empty:
            return df["catalog_owner"].iloc[0]
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Table metadata (owner + comment) — reused by governance checks
# --------------------------------------------------------------------------- #
def build_table_metadata(tables: list[str]) -> dict:
    """Return ``{full_name: {owner, comment}}`` (one query per schema)."""
    by_schema: dict[tuple, list[str]] = defaultdict(list)
    for full_name in tables:
        catalog, schema, _ = split_name(full_name)
        if catalog:
            by_schema[(catalog, schema)].append(full_name)

    meta: dict[str, dict] = {}
    for (catalog, schema), schema_tables in by_schema.items():
        df = _fetch_table_meta(catalog, schema)
        rows = ({str(r.get("table_name")): r for _, r in df.iterrows()}
                if not df.empty else {})
        for full_name in schema_tables:
            _, _, table = split_name(full_name)
            r = rows.get(table)
            owner = (r.get("table_owner") if r is not None else None)
            comment = (r.get("comment") if r is not None else None)
            meta[full_name] = {
                "owner": None if owner in (None, "") else str(owner),
                "comment": None if comment in (None, "") else str(comment),
            }
    return meta


def _grant_status(grantee, priv, source) -> str:
    """Flag broad principals holding anything beyond a USE privilege."""
    if str(grantee).lower() in _BROAD_PRINCIPALS and str(priv).upper() not in (
        "USE", "USE_CATALOG", "USE_SCHEMA"
    ):
        return STATUS_UNEXPECTED
    return STATUS_GRANTED


def build_permissions_frame(
    tables: list[str], layer_rules: list[dict] | None = None,
    metadata: dict | None = None,
) -> pd.DataFrame:
    """Build the effective-access DataFrame for every table in the chain.

    Grants (direct + inherited) are fetched **once per distinct schema**; owners
    are added from ``metadata`` (table owner) plus one schema/catalog owner
    lookup per scope. Columns: table, layer, grantor, grantee, privilege,
    is_grantable, source, principal_type, status, has_access. Tables with no
    effective access get one synthetic ``missing`` row.
    """
    metadata = metadata if metadata is not None else build_table_metadata(tables)

    by_schema: dict[tuple, list[str]] = defaultdict(list)
    non_three: list[str] = []
    for full_name in tables:
        catalog, schema, _ = split_name(full_name)
        if not catalog:
            non_three.append(full_name)
        else:
            by_schema[(catalog, schema)].append(full_name)

    rows: list[dict] = []
    catalog_owner_cache: dict[str, str | None] = {}
    for (catalog, schema), schema_tables in by_schema.items():
        grants = _fetch_schema_privileges(catalog, schema)
        grants_by_table = (
            {name: sub for name, sub in grants.groupby("table_name")}
            if not grants.empty else {}
        )
        schema_owner = _fetch_schema_owner(catalog, schema)
        if catalog not in catalog_owner_cache:
            catalog_owner_cache[catalog] = _fetch_catalog_owner(catalog)
        catalog_owner = catalog_owner_cache[catalog]

        for full_name in schema_tables:
            layer = classify_layer(full_name, layer_rules)
            _, _, table = split_name(full_name)
            table_rows: list[dict] = []

            # Owners (implicit full access) — table, then schema, then catalog.
            # Dedup the same principal across levels, keeping the most specific.
            owner = (metadata.get(full_name) or {}).get("owner")
            seen_owners: set[str] = set()
            for lvl, principal in (("table owner", owner),
                                   ("schema owner", schema_owner),
                                   ("catalog owner", catalog_owner)):
                if principal and principal not in seen_owners:
                    seen_owners.add(principal)
                    table_rows.append(_owner_row(full_name, layer, principal, lvl))

            # Direct + inherited grants.
            sub = grants_by_table.get(table)
            if sub is not None and not sub.empty:
                for _, g in sub.iterrows():
                    grantee = g.get("grantee")
                    priv = g.get("privilege_type")
                    source = _INHERITED_MAP.get(
                        str(g.get("inherited_from")).upper(), SOURCE_DIRECT)
                    table_rows.append({
                        "table": full_name,
                        "layer": layer,
                        "grantor": g.get("grantor"),
                        "grantee": grantee,
                        "privilege": priv,
                        "is_grantable": g.get("is_grantable"),
                        "source": source,
                        "principal_type": "unknown",
                        "status": _grant_status(grantee, priv, source),
                        "has_access": True,
                    })

            rows.extend(table_rows or [_gap_row(full_name, layer)])

    for full_name in non_three:
        rows.append(_gap_row(full_name, classify_layer(full_name, layer_rules)))

    return pd.DataFrame(rows, columns=_PERM_COLUMNS)


def _owner_row(full_name: str, layer: str, principal: str, level: str) -> dict:
    return {
        "table": full_name,
        "layer": layer,
        "grantor": level,          # e.g. "table owner"
        "grantee": principal,
        "privilege": "OWN",
        "is_grantable": True,
        "source": SOURCE_OWNER,
        "principal_type": "unknown",
        "status": STATUS_GRANTED,
        "has_access": True,
    }


def _gap_row(full_name: str, layer: str) -> dict:
    return {
        "table": full_name,
        "layer": layer,
        "grantor": None,
        "grantee": None,
        "privilege": None,
        "is_grantable": None,
        "source": None,
        "principal_type": "unknown",
        "status": STATUS_MISSING,
        "has_access": False,
    }


def annotate_principal_types(perms: pd.DataFrame, lookup: dict) -> pd.DataFrame:
    """Fill the ``principal_type`` column from the SCIM lookup + heuristics."""
    if perms.empty:
        return perms
    perms = perms.copy()
    perms["principal_type"] = perms["grantee"].map(
        lambda g: classify_principal(g, lookup)
    )
    return perms


def find_permission_gaps(perms: pd.DataFrame) -> pd.DataFrame:
    """Return gap/over-permission findings with a recommendation each."""
    cols = ["table", "layer", "issue", "recommendation"]
    if perms.empty:
        return pd.DataFrame(columns=cols)

    findings = []

    # No-access tables.
    reach = perms.groupby("table")["has_access"].max()
    for full_name in reach[~reach.astype(bool)].index:
        layer = perms.loc[perms["table"] == full_name, "layer"].iloc[0]
        findings.append(
            {
                "table": full_name,
                "layer": layer,
                "issue": "No grants or owner found — unreachable by consumers.",
                "recommendation": f"GRANT SELECT ON TABLE {full_name} "
                "TO `<consumer_group>`;",
            }
        )

    # Over-permissioned (broad principal) tables.
    over = perms[perms["status"] == STATUS_UNEXPECTED]
    for _, r in over.iterrows():
        src = r.get("source")
        where = (f" (inherited from {src.lower()})"
                 if src in (SOURCE_SCHEMA, SOURCE_CATALOG) else "")
        findings.append(
            {
                "table": r["table"],
                "layer": r["layer"],
                "issue": f"Broad grant: {r['grantee']} has "
                f"{r['privilege']}{where}.",
                "recommendation": _revoke_hint(r),
            }
        )

    return pd.DataFrame(findings, columns=cols)


def _revoke_hint(r) -> str:
    """REVOKE targets the object the grant is actually defined on."""
    src = r.get("source")
    catalog, schema, _ = split_name(r["table"])
    if src == SOURCE_CATALOG:
        return (f"REVOKE {r['privilege']} ON CATALOG {catalog} "
                f"FROM `{r['grantee']}`;")
    if src == SOURCE_SCHEMA:
        return (f"REVOKE {r['privilege']} ON SCHEMA {catalog}.{schema} "
                f"FROM `{r['grantee']}`;")
    return (f"REVOKE {r['privilege']} ON TABLE {r['table']} "
            f"FROM `{r['grantee']}`;")


def permissions_stats(perms: pd.DataFrame) -> dict:
    """Access counts (total + per principal type) and permission-gap count."""
    if perms.empty:
        return {"groups_with_access": 0, "permission_gaps": 0,
                "users": 0, "groups": 0, "service_principals": 0}
    accessible = perms[perms["has_access"]]
    grantees = accessible[["grantee", "principal_type"]].dropna(
        subset=["grantee"]).drop_duplicates("grantee")
    by_type = grantees["principal_type"].value_counts().to_dict()
    reach = perms.groupby("table")["has_access"].max()
    gaps = int((~reach.astype(bool)).sum())
    over = int((perms["status"] == STATUS_UNEXPECTED).sum())
    return {
        "groups_with_access": int(len(grantees)),
        "permission_gaps": gaps + over,
        "users": int(by_type.get("user", 0)),
        "groups": int(by_type.get("group", 0)),
        "service_principals": int(by_type.get("service_principal", 0)),
    }


def list_grantees(perms: pd.DataFrame) -> list[str]:
    """Distinct grantees present in the chain — powers the group filter."""
    if perms.empty:
        return []
    return sorted(perms["grantee"].dropna().astype(str).unique().tolist())


# --------------------------------------------------------------------------- #
# Schema-level helpers for the large-schema narrowing gate (single query each)
# --------------------------------------------------------------------------- #
def list_schema_grantees(catalog: str, schema: str | None) -> list[str]:
    """Distinct grantees (direct + inherited) across a schema.

    ``schema`` None means every schema in the catalog ("All schemas").
    """
    safe_c = (catalog or "").replace("`", "")
    safe_s = (schema or "").replace("'", "")
    if not safe_c:
        return []
    where = f"WHERE table_schema = '{safe_s}'" if safe_s else ""
    try:
        df = run_query(f"""
            SELECT DISTINCT grantee
            FROM `{safe_c}`.information_schema.table_privileges
            {where}
        """)
    except Exception:
        return []
    if df.empty:
        return []
    return sorted(df["grantee"].dropna().astype(str).unique().tolist())


def tables_for_grantee(catalog: str, schema: str | None,
                       grantee: str) -> list[str]:
    """Full names of tables in a schema (or whole catalog) ``grantee`` reaches."""
    safe_c = (catalog or "").replace("`", "")
    safe_s = (schema or "").replace("'", "")
    safe_g = (grantee or "").replace("'", "")
    if not (safe_c and safe_g):
        return []
    schema_clause = f"table_schema = '{safe_s}' AND " if safe_s else ""
    try:
        df = run_query(f"""
            SELECT DISTINCT table_schema, table_name
            FROM `{safe_c}`.information_schema.table_privileges
            WHERE {schema_clause}grantee = '{safe_g}'
        """)
    except Exception:
        return []
    if df.empty:
        return []
    return [f"{catalog}.{sch}.{t}" for sch, t in
            zip(df["table_schema"].astype(str), df["table_name"].astype(str))]


# --------------------------------------------------------------------------- #
# Catalog- / schema-level grants (independent of the lineage graph)
#
# Workspace admins need to see who was granted what on a catalog or schema
# without first picking a table. These read the metastore-wide
# ``system.information_schema`` privilege views, so they work for any catalog
# the query identity can see.
# --------------------------------------------------------------------------- #
LEVEL_CATALOG = "CATALOG"
LEVEL_SCHEMA = "SCHEMA"
LEVEL_TABLE = "TABLE"

SOURCE_METASTORE = "METASTORE"

_SCOPE_COLUMNS = ["level", "securable", "catalog", "schema", "grantee",
                  "privilege", "source", "grantor", "is_grantable"]


def source_from_inherited(value) -> str:
    """Map an ``inherited_from`` value (NONE/CATALOG/SCHEMA/...) to a source."""
    v = str(value or "NONE").upper()
    if v == "NONE":
        return SOURCE_DIRECT
    return {"CATALOG": SOURCE_CATALOG, "SCHEMA": SOURCE_SCHEMA,
            "METASTORE": SOURCE_METASTORE}.get(v, v)


def scope_grants(catalog: str | None = None, schema: str | None = None,
                 include_inherited: bool = False,
                 limit: int = 2000) -> pd.DataFrame:
    """Grants + owners at catalog and schema level for the picked scope.

    * ``catalog`` set: that catalog's grants/owner, plus grants/owners on its
      schemas (only ``schema`` when given, else every schema).
    * ``catalog`` None ("All catalogs"): catalog-level grants/owners across
      every visible catalog (schema rows are skipped — too broad).

    Inherited rows (e.g. a catalog grant echoed on each schema) are dropped
    unless ``include_inherited``. Columns: level, securable, catalog, schema,
    grantee, privilege, source, grantor, is_grantable.
    """
    rows: list[dict] = []
    params: dict = {}
    cat_where = ""
    if catalog:
        params["catalog"] = catalog
        cat_where = "WHERE catalog_name = :catalog"

    cats = run_query(
        "SELECT catalog_name, grantor, grantee, privilege_type, is_grantable, "
        "inherited_from FROM system.information_schema.catalog_privileges "
        f"{cat_where} ORDER BY catalog_name, grantee LIMIT {int(limit)}",
        params)
    for _, r in cats.iterrows():
        rows.append({
            "level": LEVEL_CATALOG, "securable": r["catalog_name"],
            "catalog": r["catalog_name"], "schema": None,
            "grantee": r["grantee"], "privilege": r["privilege_type"],
            "source": source_from_inherited(r.get("inherited_from")),
            "grantor": r.get("grantor"), "is_grantable": r.get("is_grantable"),
        })
    owners = run_query(
        "SELECT catalog_name, catalog_owner FROM system.information_schema."
        f"catalogs {cat_where} LIMIT {int(limit)}", params)
    for _, r in owners.iterrows():
        if r.get("catalog_owner"):
            rows.append({
                "level": LEVEL_CATALOG, "securable": r["catalog_name"],
                "catalog": r["catalog_name"], "schema": None,
                "grantee": r["catalog_owner"], "privilege": "OWN",
                "source": SOURCE_OWNER, "grantor": "catalog owner",
                "is_grantable": True,
            })

    if catalog:
        sch_where = ("WHERE catalog_name = :catalog "
                     "AND schema_name <> 'information_schema'")
        if schema:
            params["schema"] = schema
            sch_where += " AND schema_name = :schema"
        schs = run_query(
            "SELECT catalog_name, schema_name, grantor, grantee, privilege_type, "
            "is_grantable, inherited_from FROM system.information_schema."
            f"schema_privileges {sch_where} ORDER BY schema_name, grantee "
            f"LIMIT {int(limit)}", params)
        for _, r in schs.iterrows():
            rows.append({
                "level": LEVEL_SCHEMA,
                "securable": f"{r['catalog_name']}.{r['schema_name']}",
                "catalog": r["catalog_name"], "schema": r["schema_name"],
                "grantee": r["grantee"], "privilege": r["privilege_type"],
                "source": source_from_inherited(r.get("inherited_from")),
                "grantor": r.get("grantor"),
                "is_grantable": r.get("is_grantable"),
            })
        sowners = run_query(
            "SELECT catalog_name, schema_name, schema_owner FROM "
            f"system.information_schema.schemata {sch_where} "
            f"LIMIT {int(limit)}", params)
        for _, r in sowners.iterrows():
            if r.get("schema_owner"):
                rows.append({
                    "level": LEVEL_SCHEMA,
                    "securable": f"{r['catalog_name']}.{r['schema_name']}",
                    "catalog": r["catalog_name"], "schema": r["schema_name"],
                    "grantee": r["schema_owner"], "privilege": "OWN",
                    "source": SOURCE_OWNER, "grantor": "schema owner",
                    "is_grantable": True,
                })

    df = pd.DataFrame(rows, columns=_SCOPE_COLUMNS)
    if not include_inherited and not df.empty:
        df = df[df["source"].isin([SOURCE_DIRECT, SOURCE_OWNER])]
    return df.reset_index(drop=True)
