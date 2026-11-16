"""Consolidated per-table governance findings ("Table Health").

Correlates the data UCViz already computes — effective grants, tags/masks, and
the heuristic PII scan — plus table owner/comment metadata into a single list of
findings per table, each mapped to a Databricks Well-Architected Lakehouse
pillar with a severity and a concrete remediation. This is what lets a user
click one table in the lineage graph and see *everything* governance-relevant
about it in one place, rather than hunting across separate sections.

Pure functions over already-fetched data — no new queries here.
"""

from __future__ import annotations

import pandas as pd

from modules.permissions_lookup import STATUS_UNEXPECTED, split_name
from modules.pii_scan import STATUS_CLASSIFIED, STATUS_UNCLASSIFIED

# Well-Architected Lakehouse pillars this app can speak to.
PILLAR_GOVERNANCE = "Data Governance"
PILLAR_SECURITY = "Security & Privacy"

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2}

_FINDING_COLUMNS = ["table", "severity", "pillar", "check", "issue",
                    "recommendation"]


def _tag_mask(full_name: str, column: str) -> str:
    return (f"ALTER TABLE {full_name} ALTER COLUMN {column} "
            "SET TAGS ('classification'='PII');  -- then apply a column mask")


def build_health_findings(
    tables: list[str], tags_summary: dict, pii_findings: pd.DataFrame,
    metadata: dict, perms: pd.DataFrame,
) -> pd.DataFrame:
    """Return per-table governance findings across ``tables``.

    Columns: table, severity, pillar, check, issue, recommendation. Ordered by
    table then severity (high first).
    """
    rows: list[dict] = []
    pii = pii_findings if pii_findings is not None else pd.DataFrame()
    over_tables = set()
    no_access_tables = set()
    if perms is not None and not perms.empty:
        over_tables = set(
            perms.loc[perms["status"] == STATUS_UNEXPECTED, "table"])
        reach = perms.groupby("table")["has_access"].max()
        no_access_tables = set(reach[~reach.astype(bool)].index)

    for full_name in tables:
        info = tags_summary.get(full_name, {}) or {}
        meta = metadata.get(full_name, {}) or {}
        masked = set(info.get("masked_columns") or [])
        table_pii = (pii[pii["table"] == full_name]
                     if not pii.empty else pd.DataFrame())

        # 1. Unclassified PII (name looks sensitive, no tag/mask). ----------- #
        for _, r in table_pii.iterrows():
            if r["status"] == STATUS_UNCLASSIFIED:
                rows.append({
                    "table": full_name, "severity": SEV_HIGH,
                    "pillar": PILLAR_GOVERNANCE,
                    "check": "Unclassified sensitive data",
                    "issue": f"Column `{r['column']}` looks like "
                             f"{r['category']} but carries no classification "
                             "tag or mask.",
                    "recommendation": r["recommendation"],
                })

        # 2. Sensitive but unmasked (classified/tagged, no CLS mask). -------- #
        unmasked_cols = set()
        for _, r in table_pii.iterrows():
            if r["status"] == STATUS_CLASSIFIED and r["column"] not in masked:
                unmasked_cols.add(r["column"])
        for ct in info.get("column_tags") or []:
            if ct.get("role") == "sensitivity" and ct.get("column") not in masked:
                unmasked_cols.add(ct.get("column"))
        for column in sorted(c for c in unmasked_cols if c):
            rows.append({
                "table": full_name, "severity": SEV_HIGH,
                "pillar": PILLAR_SECURITY,
                "check": "Sensitive column not masked",
                "issue": f"Column `{column}` is classified as sensitive but has "
                         "no column mask (CLS) applied.",
                "recommendation": f"ALTER TABLE {full_name} ALTER COLUMN "
                                  f"{column} SET MASK <mask_func>;",
            })

        # 3. No tags / no classification at all. ----------------------------- #
        has_sensitivity = any(
            t.get("role") == "sensitivity"
            for t in (info.get("tags") or []) + (info.get("column_tags") or [])
        )
        if not info.get("tags") and not has_sensitivity:
            rows.append({
                "table": full_name, "severity": SEV_MEDIUM,
                "pillar": PILLAR_GOVERNANCE,
                "check": "No tags / classification",
                "issue": "Table has no tags and no sensitivity classification — "
                         "hard to discover and govern.",
                "recommendation": f"ALTER TABLE {full_name} SET TAGS "
                                  "('domain'='<domain>','sensitivity'='<level>');",
            })

        # 4. No owner. ------------------------------------------------------- #
        if not meta.get("owner"):
            rows.append({
                "table": full_name, "severity": SEV_MEDIUM,
                "pillar": PILLAR_GOVERNANCE,
                "check": "No owner set",
                "issue": "Table has no owner — no clear accountability.",
                "recommendation": f"ALTER TABLE {full_name} OWNER TO "
                                  "`<owning_group>`;",
            })

        # 5. Missing documentation. ------------------------------------------ #
        if not meta.get("comment"):
            rows.append({
                "table": full_name, "severity": SEV_LOW,
                "pillar": PILLAR_GOVERNANCE,
                "check": "Missing documentation",
                "issue": "Table has no description/comment.",
                "recommendation": f"COMMENT ON TABLE {full_name} IS "
                                  "'<what this table is>';",
            })

        # 6. Over-permissioned (broad principal). ---------------------------- #
        if full_name in over_tables:
            rows.append({
                "table": full_name, "severity": SEV_HIGH,
                "pillar": PILLAR_SECURITY,
                "check": "Over-permissioned",
                "issue": "A broad principal (e.g. account users / all users) "
                         "has access to this table.",
                "recommendation": "Review the Permissions panel and REVOKE the "
                                  "broad grant; grant to specific groups instead.",
            })

        # 7. No effective access (orphaned). --------------------------------- #
        if full_name in no_access_tables:
            rows.append({
                "table": full_name, "severity": SEV_MEDIUM,
                "pillar": PILLAR_GOVERNANCE,
                "check": "No effective access",
                "issue": "No grants or owner found — unreachable by consumers.",
                "recommendation": f"GRANT SELECT ON TABLE {full_name} TO "
                                  "`<consumer_group>`;",
            })

    df = pd.DataFrame(rows, columns=_FINDING_COLUMNS)
    if not df.empty:
        df["_s"] = df["severity"].map(_SEV_ORDER).fillna(9)
        df = df.sort_values(["table", "_s", "check"]).drop(columns=["_s"])
    return df


def health_stats(findings: pd.DataFrame) -> dict:
    """Counts by severity plus a total for the stats row."""
    if findings is None or findings.empty:
        return {"total": 0, "high": 0, "medium": 0, "low": 0}
    counts = findings["severity"].value_counts().to_dict()
    return {
        "total": int(len(findings)),
        "high": int(counts.get(SEV_HIGH, 0)),
        "medium": int(counts.get(SEV_MEDIUM, 0)),
        "low": int(counts.get(SEV_LOW, 0)),
    }
