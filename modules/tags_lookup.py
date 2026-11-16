"""Tags, classification, RLS and CLS lookups over ``information_schema``.

Reads table/column/schema tags, row-level-security filters, column-level
masks, and (best-effort) ABAC policy definitions. Tag keys are mapped to
semantic roles (domain / sensitivity / abac) via admin-managed config so tag
coloring is not tied to any customer's naming.
"""

from __future__ import annotations

import pandas as pd

from modules.db import run_query
from modules.permissions_lookup import split_name

# Roles used for pill coloring.
ROLE_DOMAIN = "domain"
ROLE_SENSITIVITY = "sensitivity"
ROLE_ABAC = "abac"
ROLE_OTHER = "other"


def role_for_tag(tag_name: str, tag_roles: dict) -> str:
    """Map a tag key to a semantic role using admin config (case-insensitive)."""
    name = (tag_name or "").lower()
    for role in (ROLE_DOMAIN, ROLE_SENSITIVITY, ROLE_ABAC):
        for key in tag_roles.get(role, []) or []:
            if str(key).lower() in name:
                return role
    return ROLE_OTHER


def _q(catalog, query) -> pd.DataFrame:
    try:
        return run_query(query)
    except Exception:
        return pd.DataFrame()


# --------------------------------------------------------------------------- #
# Schema-level helpers for the large-schema narrowing gate (single query each)
# --------------------------------------------------------------------------- #
def list_schema_tag_values(catalog: str, schema: str | None,
                           tag_roles: dict) -> list[str]:
    """Distinct domain/sensitivity tag values in a schema, as ``name=value``.

    One query over ``table_tags``; used to populate the "narrow by tag" filter
    when a schema is too large to graph whole. ``schema`` None means the whole
    catalog ("All schemas").
    """
    safe_c = (catalog or "").replace("`", "")
    safe_s = (schema or "").replace("'", "")
    if not safe_c:
        return []
    where = f"WHERE schema_name = '{safe_s}'" if safe_s else ""
    df = _q(safe_c, f"""
        SELECT DISTINCT tag_name, tag_value
        FROM `{safe_c}`.information_schema.table_tags
        {where}
    """)
    if df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        name = r.get("tag_name")
        if role_for_tag(name, tag_roles) in (ROLE_DOMAIN, ROLE_SENSITIVITY):
            out.append(f"{name}={r.get('tag_value')}")
    return sorted(set(out))


def tables_with_tag(catalog: str, schema: str | None, tag_name: str,
                    tag_value: str) -> list[str]:
    """Full names of tables in a schema (or whole catalog) with a tag value."""
    safe_c = (catalog or "").replace("`", "")
    safe_s = (schema or "").replace("'", "")
    safe_n = (tag_name or "").replace("'", "")
    safe_v = (tag_value or "").replace("'", "")
    if not (safe_c and safe_n):
        return []
    schema_clause = f"schema_name = '{safe_s}' AND " if safe_s else ""
    df = _q(safe_c, f"""
        SELECT DISTINCT schema_name, table_name
        FROM `{safe_c}`.information_schema.table_tags
        WHERE {schema_clause}tag_name = '{safe_n}'
          AND tag_value = '{safe_v}'
    """)
    if df.empty:
        return []
    return [f"{catalog}.{sch}.{t}" for sch, t in
            zip(df["schema_name"].astype(str), df["table_name"].astype(str))]


def fetch_table_tags(full_name: str) -> pd.DataFrame:
    catalog, schema, table = split_name(full_name)
    if not catalog:
        return pd.DataFrame(columns=["tag_name", "tag_value"])
    return _q(
        catalog,
        f"""
        SELECT tag_name, tag_value
        FROM `{catalog}`.information_schema.table_tags
        WHERE schema_name = '{schema}' AND table_name = '{table}'
        """,
    )


def fetch_column_tags(full_name: str) -> pd.DataFrame:
    catalog, schema, table = split_name(full_name)
    if not catalog:
        return pd.DataFrame(columns=["column_name", "tag_name", "tag_value"])
    return _q(
        catalog,
        f"""
        SELECT column_name, tag_name, tag_value
        FROM `{catalog}`.information_schema.column_tags
        WHERE schema_name = '{schema}' AND table_name = '{table}'
        """,
    )


def fetch_row_filters(full_name: str) -> pd.DataFrame:
    """Row-level-security filters bound to the table (RLS)."""
    catalog, schema, table = split_name(full_name)
    if not catalog:
        return pd.DataFrame()
    return _q(
        catalog,
        f"""
        SELECT *
        FROM `{catalog}`.information_schema.row_filters
        WHERE table_schema = '{schema}' AND table_name = '{table}'
        """,
    )


def fetch_column_masks(full_name: str) -> pd.DataFrame:
    """Column-level-security masks bound to the table (CLS)."""
    catalog, schema, table = split_name(full_name)
    if not catalog:
        return pd.DataFrame()
    return _q(
        catalog,
        f"""
        SELECT *
        FROM `{catalog}`.information_schema.column_masks
        WHERE table_schema = '{schema}' AND table_name = '{table}'
        """,
    )


def fetch_abac_policies(limit: int = 500) -> pd.DataFrame:
    """Best-effort read of ABAC policy definitions (may not exist yet).

    Bounded by ``limit`` — this view can be slow to scan on large metastores.
    """
    return _q(
        "system",
        f"""
        SELECT *
        FROM system.information_schema.abac_policy_definitions
        LIMIT {int(limit)}
        """,
    )


def _fetch_schema_frame(catalog, schema, view, schema_col) -> pd.DataFrame:
    """One query for a whole schema from an information_schema ``view``."""
    return _q(
        catalog,
        f"SELECT * FROM `{catalog}`.information_schema.{view} "
        f"WHERE {schema_col} = '{schema}'",
    )


def build_tags_summary(
    tables: list[str], tag_roles: dict, high_values: list[str] | None = None
) -> dict:
    """Assemble per-table tag/RLS/CLS info for the UI.

    Tags, row filters, and column masks are fetched **once per distinct schema**
    (not per table), so a whole-schema view stays fast. Returns
    ``{full_name: {tags, column_tags, rls, cls, masked_columns}}``.
    """
    from collections import defaultdict

    high_values = {str(v).lower() for v in (high_values or [])}
    summary: dict[str, dict] = {}

    by_schema: dict[tuple, list[str]] = defaultdict(list)
    for full_name in tables:
        catalog, schema, _ = split_name(full_name)
        summary[full_name] = {"tags": [], "column_tags": [], "rls": False,
                              "cls": 0, "masked_columns": []}
        if catalog:
            by_schema[(catalog, schema)].append(full_name)

    def _col(df, *candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    for (catalog, schema), schema_tables in by_schema.items():
        want = {split_name(t)[2]: t for t in schema_tables}  # table_name -> full

        tt = _fetch_schema_frame(catalog, schema, "table_tags", "schema_name")
        if not tt.empty:
            tn = _col(tt, "table_name")
            for _, r in tt.iterrows():
                full = want.get(str(r.get(tn)))
                if not full:
                    continue
                value = r.get("tag_value")
                summary[full]["tags"].append({
                    "name": r.get("tag_name"), "value": value,
                    "role": role_for_tag(r.get("tag_name"), tag_roles),
                    "high": str(value).lower() in high_values,
                })

        ct = _fetch_schema_frame(catalog, schema, "column_tags", "schema_name")
        if not ct.empty:
            tn = _col(ct, "table_name")
            for _, r in ct.iterrows():
                full = want.get(str(r.get(tn)))
                if not full:
                    continue
                value = r.get("tag_value")
                summary[full]["column_tags"].append({
                    "column": r.get("column_name"), "name": r.get("tag_name"),
                    "value": value,
                    "role": role_for_tag(r.get("tag_name"), tag_roles),
                    "high": str(value).lower() in high_values,
                })

        rf = _fetch_schema_frame(catalog, schema, "row_filters", "table_schema")
        if not rf.empty:
            tn = _col(rf, "table_name")
            for name in rf[tn].astype(str) if tn else []:
                full = want.get(name)
                if full:
                    summary[full]["rls"] = True

        cm = _fetch_schema_frame(catalog, schema, "column_masks", "table_schema")
        if not cm.empty:
            tn = _col(cm, "table_name")
            cc = _col(cm, "column_name")
            if tn:
                for _, r in cm.iterrows():
                    full = want.get(str(r.get(tn)))
                    if not full:
                        continue
                    summary[full]["cls"] += 1
                    if cc:
                        summary[full]["masked_columns"].append(str(r.get(cc)))

    return summary


def tags_stats(summary: dict) -> dict:
    """Counters for the stats row: distinct tags, RLS tables, masked columns."""
    tag_values = set()
    rls_tables = 0
    masked_cols = 0
    for info in summary.values():
        for t in info["tags"]:
            tag_values.add((t["name"], t["value"]))
        for t in info["column_tags"]:
            tag_values.add((t["name"], t["value"]))
        if info["rls"]:
            rls_tables += 1
        masked_cols += len(info["masked_columns"])
    return {
        "distinct_tags": len(tag_values),
        "rls_tables": rls_tables,
        "masked_columns": masked_cols,
    }
