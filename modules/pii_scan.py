"""Heuristic sensitive-data (PII) scan over lineage-chain columns.

Reads each table's columns from ``information_schema.columns`` (one query per
schema) and flags column *names* that match well-known sensitive categories —
even when they carry no classification tag. Each finding cross-references the
column's existing tags and masks so the UI can separate "already governed" from
"unclassified — review", and emits a concrete remediation (tag + mask).

This is name-based heuristic detection (no data is read), so it surfaces
*candidates* for human review, not confirmed PII.
"""

from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from modules.db import run_query
from modules.permissions_lookup import split_name

STATUS_UNCLASSIFIED = "Unclassified — review"
STATUS_CLASSIFIED = "Classified"
STATUS_MASKED = "Masked"

# Ordered rules (strongest first). Each: category, keywords, why, severity.
# Keywords are matched as whole words/phrases against the normalized column
# name (camelCase and separators handled), which avoids false hits like
# 'age' inside 'usage' or 'tin' inside 'routing'.
PII_RULES: list[tuple[str, list[str], str, str]] = [
    ("Credential/Secret",
     ["password", "passwd", "pwd", "secret", "api key", "apikey", "access key",
      "secret key", "private key", "token", "client secret", "credential"],
     "looks like a credential or secret", "high"),
    ("Government ID",
     ["ssn", "social security", "national id", "aadhaar", "passport",
      "tax id", "tin", "ein", "sin", "pan number", "driver license",
      "license number", "dl number", "voter id"],
     "looks like a government-issued identifier", "high"),
    ("Financial",
     ["credit card", "creditcard", "card number", "cardnumber", "ccn", "cvv",
      "cvc", "iban", "swift", "routing number", "aba routing",
      "account number", "acct no", "bank account", "salary", "income",
      "compensation", "payroll", "wage"],
     "looks like financial / payment data", "high"),
    ("Health (PHI)",
     ["diagnosis", "icd", "mrn", "medical record", "patient", "health",
      "disease", "prescription", "treatment", "provider npi"],
     "looks like protected health information", "high"),
    ("Email",
     ["email", "e mail", "email address"],
     "looks like an email address", "high"),
    ("Phone",
     ["phone", "mobile", "cell", "telephone", "fax", "msisdn"],
     "looks like a phone number", "high"),
    ("Date of Birth",
     ["dob", "date of birth", "birth date", "birthdate", "birthday"],
     "looks like a date of birth", "high"),
    ("Person Name",
     ["first name", "last name", "full name", "fname", "lname", "middle name",
      "surname", "given name", "maiden name", "person name", "contact name"],
     "looks like a person's name", "medium"),
    ("Online Identifier",
     ["ip address", "ipaddr", "mac address", "device id", "imei", "cookie id",
      "session id", "advertising id"],
     "looks like an online / device identifier", "medium"),
    ("Postal Address",
     ["address", "street", "zipcode", "zip code", "postal code", "postcode",
      "addr line"],
     "looks like a postal address", "medium"),
    ("Geolocation",
     ["latitude", "longitude", "geo lat", "geo long", "coordinates", "geohash"],
     "looks like precise geolocation", "medium"),
    ("Demographic",
     ["gender", "ethnicity", "race", "religion", "nationality",
      "marital status"],
     "looks like a sensitive demographic attribute", "medium"),
]


def _normalize(name: str) -> str:
    """Lowercase, split camelCase, collapse separators to single spaces."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name or "")
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).lower().strip()
    return s


def detect(column_name: str) -> tuple[str, str, str] | None:
    """Return (category, why, severity) for the strongest match, else None."""
    norm = _normalize(column_name)
    if not norm:
        return None
    for category, keywords, why, severity in PII_RULES:
        for kw in keywords:
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", norm):
                return category, f"column name '{column_name}' {why}", severity
    return None


def _fetch_schema_columns(catalog: str, schema: str) -> pd.DataFrame:
    q = f"""
        SELECT table_name, column_name, data_type
        FROM `{catalog}`.information_schema.columns
        WHERE table_schema = '{schema}'
    """
    try:
        return run_query(q)
    except Exception:
        return pd.DataFrame(columns=["table_name", "column_name", "data_type"])


def _status(full_name: str, column: str, tags_summary: dict) -> str:
    info = tags_summary.get(full_name) or {}
    if column in (info.get("masked_columns") or []):
        return STATUS_MASKED
    for ct in info.get("column_tags") or []:
        if ct.get("column") == column and ct.get("role") == "sensitivity":
            return STATUS_CLASSIFIED
    return STATUS_UNCLASSIFIED


def _recommendation(full_name: str, column: str, status: str) -> str:
    if status == STATUS_UNCLASSIFIED:
        return (f"ALTER TABLE {full_name} ALTER COLUMN {column} "
                "SET TAGS ('classification'='PII');  -- then apply a column mask")
    if status == STATUS_CLASSIFIED:
        return (f"Tagged but unmasked — consider: ALTER TABLE {full_name} "
                f"ALTER COLUMN {column} SET MASK <mask_func>;")
    return "Protected (masked)."


def scan_columns(tables: list[str], tags_summary: dict) -> pd.DataFrame:
    """Return sensitive-column findings across the given tables.

    Columns: table, column, data_type, category, why, severity, status,
    recommendation.
    """
    by_schema: dict[tuple, list[str]] = defaultdict(list)
    for full_name in tables:
        catalog, schema, _ = split_name(full_name)
        if catalog:
            by_schema[(catalog, schema)].append(full_name)

    rows: list[dict] = []
    for (catalog, schema), schema_tables in by_schema.items():
        cols = _fetch_schema_columns(catalog, schema)
        if cols.empty:
            continue
        want = {split_name(t)[2]: t for t in schema_tables}
        for _, c in cols.iterrows():
            full = want.get(str(c.get("table_name")))
            if not full:
                continue
            column = str(c.get("column_name"))
            hit = detect(column)
            if not hit:
                continue
            category, why, severity = hit
            status = _status(full, column, tags_summary)
            rows.append({
                "table": full,
                "column": column,
                "data_type": c.get("data_type"),
                "category": category,
                "why": why,
                "severity": severity,
                "status": status,
                "recommendation": _recommendation(full, column, status),
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        # Surface unclassified + high severity first.
        order_status = {STATUS_UNCLASSIFIED: 0, STATUS_CLASSIFIED: 1,
                        STATUS_MASKED: 2}
        order_sev = {"high": 0, "medium": 1, "low": 2}
        df["_s"] = df["status"].map(order_status).fillna(9)
        df["_v"] = df["severity"].map(order_sev).fillna(9)
        df = df.sort_values(["_s", "_v", "table", "column"]).drop(
            columns=["_s", "_v"])
    return df


def pii_stats(findings: pd.DataFrame) -> dict:
    """Counters: total sensitive columns and unclassified (the alert count)."""
    if findings.empty:
        return {"sensitive_columns": 0, "unclassified_pii": 0}
    unclassified = int((findings["status"] == STATUS_UNCLASSIFIED).sum())
    return {"sensitive_columns": int(len(findings)),
            "unclassified_pii": unclassified}
