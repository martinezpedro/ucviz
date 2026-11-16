"""Principal-centric (reverse) access view — "what can this principal reach?".

The main page goes data -> principals (pick a table, see who has access). This
module goes the other way: pick a group, user or service principal and list
every catalog, schema and table it holds a privilege on, or owns.

Reads the metastore-wide ``system.information_schema`` views:

* ``catalog_privileges`` / ``schema_privileges`` / ``table_privileges`` — grants,
  with ``inherited_from`` telling a direct grant (NONE) from one inherited from
  the parent CATALOG / SCHEMA (``table_privileges`` echoes a catalog-level
  SELECT on every table, which is what makes the table drill-down "effective").
* ``catalogs`` / ``schemata`` / ``tables`` — owners (implicit full access;
  owners never appear in the privilege views).

For a **user**, access granted to the groups they belong to can be folded in
(``via`` = ``group: <name>``) — direct SCIM memberships plus ``account users``.

Like every other information_schema read, results only cover objects the query
identity can see, in catalogs bound to this workspace.
"""

from __future__ import annotations

import pandas as pd

from modules.db import run_query
from modules.permissions_lookup import (
    LEVEL_CATALOG,
    LEVEL_SCHEMA,
    LEVEL_TABLE,
    SOURCE_DIRECT,
    SOURCE_OWNER,
    source_from_inherited,
)

ACCESS_COLUMNS = ["level", "catalog", "schema", "table", "securable",
                  "grantee", "via", "privilege", "source", "is_grantable"]

# Per-view row cap; a catalog-level grant echoes onto every table, so a broad
# principal can match a lot of table rows.
ROW_LIMIT = 5000


def list_known_grantees(catalogs: list[str] | None = None) -> list[str]:
    """Distinct grantees holding catalog/schema grants (picker options).

    Table-level grantees are skipped on purpose — ``table_privileges`` can be
    very large metastore-wide; such principals can still be typed in.
    """
    where, params = _catalog_filter("catalog_name", catalogs)
    df = run_query(
        f"SELECT DISTINCT grantee FROM system.information_schema."
        f"catalog_privileges {where} UNION "
        f"SELECT DISTINCT grantee FROM system.information_schema."
        f"schema_privileges {where}", params)
    if df.empty:
        return []
    return sorted(df["grantee"].dropna().astype(str).unique().tolist())


def _catalog_filter(column: str, catalogs, prefix: str = "WHERE"):
    """``(sql, params)`` restricting ``column`` to the admin catalog list."""
    if not catalogs:
        return "", {}
    params = {f"cat{i}": c for i, c in enumerate(catalogs)}
    keys = ", ".join(f":cat{i}" for i in range(len(catalogs)))
    return f"{prefix} {column} IN ({keys})", params


def _in_clause(column: str, values: list[str], key: str):
    params = {f"{key}{i}": v for i, v in enumerate(values)}
    keys = ", ".join(f":{key}{i}" for i in range(len(values)))
    return f"{column} IN ({keys})", params


def principal_access(grantees: dict[str, str],
                     catalogs: list[str] | None = None,
                     limit: int = ROW_LIMIT) -> tuple[pd.DataFrame, bool]:
    """Everything the given grantees can reach, one row per privilege.

    ``grantees`` maps a grantee string to a ``via`` label — ``""`` for the
    principal itself, ``"group: <name>"`` for a group it belongs to. Returns
    ``(frame, truncated)`` where ``truncated`` means a view hit ``limit``.
    """
    names = [g for g in grantees if g]
    if not names:
        return pd.DataFrame(columns=ACCESS_COLUMNS), False

    rows: list[dict] = []
    truncated = False

    def add(level, cat, sch, tbl, grantee, priv, source, grantable):
        securable = ".".join(p for p in (cat, sch, tbl) if p)
        rows.append({
            "level": level, "catalog": cat, "schema": sch, "table": tbl,
            "securable": securable, "grantee": grantee,
            "via": grantees.get(grantee, ""), "privilege": priv,
            "source": source, "is_grantable": grantable,
        })

    def query(sql, params):
        nonlocal truncated
        df = run_query(sql + f" LIMIT {int(limit)}", params)
        if len(df) >= limit:
            truncated = True
        return df

    # --- Grants ---------------------------------------------------------- #
    g_sql, g_params = _in_clause("grantee", names, "g")
    c_sql, c_params = _catalog_filter("catalog_name", catalogs, "AND")
    df = query(
        "SELECT grantee, catalog_name, privilege_type, is_grantable, "
        "inherited_from FROM system.information_schema.catalog_privileges "
        f"WHERE {g_sql} {c_sql}", {**g_params, **c_params})
    for _, r in df.iterrows():
        add(LEVEL_CATALOG, r["catalog_name"], None, None, r["grantee"],
            r["privilege_type"], source_from_inherited(r.get("inherited_from")),
            r.get("is_grantable"))

    df = query(
        "SELECT grantee, catalog_name, schema_name, privilege_type, "
        "is_grantable, inherited_from FROM system.information_schema."
        f"schema_privileges WHERE {g_sql} "
        f"AND schema_name <> 'information_schema' {c_sql}",
        {**g_params, **c_params})
    for _, r in df.iterrows():
        add(LEVEL_SCHEMA, r["catalog_name"], r["schema_name"], None,
            r["grantee"], r["privilege_type"],
            source_from_inherited(r.get("inherited_from")),
            r.get("is_grantable"))

    tc_sql, tc_params = _catalog_filter("table_catalog", catalogs, "AND")
    df = query(
        "SELECT grantee, table_catalog, table_schema, table_name, "
        "privilege_type, is_grantable, inherited_from FROM "
        f"system.information_schema.table_privileges WHERE {g_sql} "
        f"{tc_sql}", {**g_params, **tc_params})
    for _, r in df.iterrows():
        add(LEVEL_TABLE, r["table_catalog"], r["table_schema"],
            r["table_name"], r["grantee"], r["privilege_type"],
            source_from_inherited(r.get("inherited_from")),
            r.get("is_grantable"))

    # --- Ownership ------------------------------------------------------- #
    o_sql, o_params = _in_clause("catalog_owner", names, "o")
    df = query(
        "SELECT catalog_name, catalog_owner FROM system.information_schema."
        f"catalogs WHERE {o_sql} {c_sql}", {**o_params, **c_params})
    for _, r in df.iterrows():
        add(LEVEL_CATALOG, r["catalog_name"], None, None, r["catalog_owner"],
            "OWN", SOURCE_OWNER, True)

    o_sql, o_params = _in_clause("schema_owner", names, "o")
    df = query(
        "SELECT catalog_name, schema_name, schema_owner FROM "
        f"system.information_schema.schemata WHERE {o_sql} "
        f"AND schema_name <> 'information_schema' {c_sql}",
        {**o_params, **c_params})
    for _, r in df.iterrows():
        add(LEVEL_SCHEMA, r["catalog_name"], r["schema_name"], None,
            r["schema_owner"], "OWN", SOURCE_OWNER, True)

    o_sql, o_params = _in_clause("table_owner", names, "o")
    df = query(
        "SELECT table_catalog, table_schema, table_name, table_owner FROM "
        f"system.information_schema.tables WHERE {o_sql} {tc_sql}",
        {**o_params, **tc_params})
    for _, r in df.iterrows():
        add(LEVEL_TABLE, r["table_catalog"], r["table_schema"],
            r["table_name"], r["table_owner"], "OWN", SOURCE_OWNER, True)

    frame = pd.DataFrame(rows, columns=ACCESS_COLUMNS).drop_duplicates()
    return frame.reset_index(drop=True), truncated


def summarize(frame: pd.DataFrame, level: str, key_cols: list[str],
              child_keys: list[str] | None = None) -> pd.DataFrame:
    """Roll ``frame`` up to one row per securable at ``level``.

    Columns: the ``key_cols``, ``direct`` (privileges granted on the object
    itself), ``inherited`` (privileges inherited from a parent), ``owner``
    (bool), ``via`` (groups the access comes through), and — when
    ``child_keys`` is given — ``children`` (distinct child objects with any
    access below this one).
    """
    keyed = frame.dropna(subset=key_cols)
    out = []
    for key, sub in keyed.groupby(key_cols):
        key = key if isinstance(key, tuple) else (key,)
        own = sub[sub["level"] == level]
        direct = sorted(set(own.loc[own["source"] == SOURCE_DIRECT,
                                    "privilege"].astype(str)))
        inherited = sorted(set(own.loc[~own["source"].isin(
            [SOURCE_DIRECT, SOURCE_OWNER]), "privilege"].astype(str)))
        row = dict(zip(key_cols, key))
        row.update({
            "direct": direct,
            "inherited": inherited,
            "owner": bool((own["source"] == SOURCE_OWNER).any()),
            # "direct" = the principal's own grant; else the group path.
            "via": sorted({v or "direct" for v in sub["via"].astype(str)}),
        })
        if child_keys:
            kids = sub.dropna(subset=child_keys).drop_duplicates(child_keys)
            row["children"] = int(len(kids))
        out.append(row)
    return pd.DataFrame(out)
