#!/usr/bin/env python3
"""Seed a synthetic medallion demo split by CATALOG (bronze / silver / gold).

OPTIONAL — for a dev/demo workspace only. UCViz itself needs no demo data; it
reads whatever is already in your Unity Catalog. Use this to try UCViz on a
known lineage that crosses catalogs, with catalog-, schema- and table-level
grants for the Principal Access page and the Catalog & Schema Grants tab.

Run from the repo folder after installing the app (INSTALL.md):

    export UCVIZ_WH=<sql-warehouse-id>             # required
    python3 scripts/seed_cross_catalog_demo.py             # seed / re-seed
    python3 scripts/seed_cross_catalog_demo.py --teardown  # remove it again

Every statement goes through the SQL Statement Execution API via the
Databricks CLI, so writes run on a SQL warehouse and Unity Catalog records the
CTAS/INSERT lineage. Lineage in system.access.table_lineage can take up to
about an hour to appear; the real-time lineage API is near-immediate.

Configuration (environment variables):
    UCVIZ_WH             SQL warehouse ID                            (required)
    UCVIZ_PROFILE        Databricks CLI profile        (default: CLI default)
    UCVIZ_APP_NAME       name of the installed app, used to look up its
                         service principal                     (default: ucviz)
    UCVIZ_APP_SP         the app's service-principal application ID — only
                         if you don't want it looked up from UCVIZ_APP_NAME
    UCVIZ_CAT_PREFIX     catalog name prefix                (default: ucviz_demo)
    UCVIZ_BRONZE_CAT / UCVIZ_SILVER_CAT / UCVIZ_GOLD_CAT
                         override each catalog name   (default: <prefix>_bronze …)
    UCVIZ_DEMO_SCHEMA    schema used in every catalog          (default: sales)
    UCVIZ_TEST_GROUPS    two comma-separated ACCOUNT groups (or users) to grant
                         to       (default: ucviz_demo_engineers,ucviz_demo_analysts)
    UCVIZ_MANAGED_LOCATION  MANAGED LOCATION for CREATE CATALOG, only if the
                         metastore has no default storage
    UCVIZ_REUSE_CATALOGS=1  don't create catalogs — put each layer in its own
                         schema inside EXISTING catalogs (e.g. when the
                         metastore is at its catalog quota). Grants are then
                         scoped to those schemas, never the whole catalog.
    UCVIZ_BRONZE_SCHEMA / UCVIZ_SILVER_SCHEMA / UCVIZ_GOLD_SCHEMA
                         per-layer schema names in reuse mode
                         (default: ucviz_xcat_bronze / _silver / _gold)
    UCVIZ_SP_MANAGE=1    also grant MANAGE on the demo schemas to the app SP
                         (lets it see other principals' grants)
    UCVIZ_SKIP_GROUPS=1  don't try to create the test groups in the workspace

The demo tags use keys `business_domain` and `classification`. If your
workspace enforces tag policies that don't allow them, those statements fail
and are reported; everything else still runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PROFILE = os.environ.get("UCVIZ_PROFILE", "").strip()
WAREHOUSE = os.environ.get("UCVIZ_WH", "").strip()
APP_NAME = os.environ.get("UCVIZ_APP_NAME", "ucviz").strip()

PREFIX = os.environ.get("UCVIZ_CAT_PREFIX", "ucviz_demo")
BRONZE = os.environ.get("UCVIZ_BRONZE_CAT", f"{PREFIX}_bronze")
SILVER = os.environ.get("UCVIZ_SILVER_CAT", f"{PREFIX}_silver")
GOLD = os.environ.get("UCVIZ_GOLD_CAT", f"{PREFIX}_gold")
SCH = os.environ.get("UCVIZ_DEMO_SCHEMA", "sales")
GROUPS = [g.strip() for g in os.environ.get(
    "UCVIZ_TEST_GROUPS", "ucviz_demo_engineers,ucviz_demo_analysts").split(",")
    if g.strip()]
ENGINEERS, ANALYSTS = (GROUPS + ["ucviz_demo_analysts"])[:2]
LOCATION = os.environ.get("UCVIZ_MANAGED_LOCATION", "").strip()


def _cli(args: list[str]) -> list[str]:
    cmd = ["databricks"] + args
    return cmd + (["-p", PROFILE] if PROFILE else [])


def _api(method, path, payload=None):
    # CLI subcommands are lowercase (post/get); --json must precede -p.
    cmd = ["api", method.lower(), path]
    if payload is not None:
        cmd += ["--json", json.dumps(payload)]
    out = subprocess.run(_cli(cmd), capture_output=True, text=True)
    if out.returncode != 0:
        return {"_error": out.stderr.strip() or out.stdout.strip()}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"_error": f"non-JSON response: {out.stdout[:300]}"}


def run(statement):
    resp = _api("POST", "/api/2.0/sql/statements", {
        "warehouse_id": WAREHOUSE, "statement": statement,
        "wait_timeout": "30s", "on_wait_timeout": "CONTINUE",
    })
    if "_error" in resp:
        return "ERROR", resp["_error"]
    sid = resp.get("statement_id")
    state = (resp.get("status") or {}).get("state")
    while state in ("PENDING", "RUNNING"):
        time.sleep(2)
        resp = _api("GET", f"/api/2.0/sql/statements/{sid}")
        if "_error" in resp:
            return "ERROR", resp["_error"]
        state = (resp.get("status") or {}).get("state")
    if state != "SUCCEEDED":
        err = (resp.get("status") or {}).get("error", {})
        return state, err.get("message", json.dumps(resp)[:300])
    return "SUCCEEDED", ""


def _app_sp() -> str:
    """The app's service-principal application ID (env, or looked up)."""
    sp = os.environ.get("UCVIZ_APP_SP", "").strip()
    if sp:
        return sp
    out = subprocess.run(_cli(["apps", "get", APP_NAME, "-o", "json"]),
                         capture_output=True, text=True)
    if out.returncode == 0:
        try:
            sp = json.loads(out.stdout).get("service_principal_client_id", "")
        except json.JSONDecodeError:
            sp = ""
    if not sp:
        sys.exit(f"Could not find the service principal of app '{APP_NAME}'. "
                 "Install the app first (INSTALL.md), set UCVIZ_APP_NAME, or "
                 "set UCVIZ_APP_SP to its application ID.")
    return sp


if not WAREHOUSE:
    sys.exit("Set UCVIZ_WH to a SQL warehouse ID (see the header of this "
             "script).")
APP_SP = _app_sp()

REUSE = os.environ.get("UCVIZ_REUSE_CATALOGS") == "1"
SP_MANAGE = os.environ.get("UCVIZ_SP_MANAGE") == "1"
SKIP_GROUPS = os.environ.get("UCVIZ_SKIP_GROUPS") == "1"
if REUSE:
    B = f"{BRONZE}.{os.environ.get('UCVIZ_BRONZE_SCHEMA', 'ucviz_xcat_bronze')}"
    S = f"{SILVER}.{os.environ.get('UCVIZ_SILVER_SCHEMA', 'ucviz_xcat_silver')}"
    G = f"{GOLD}.{os.environ.get('UCVIZ_GOLD_SCHEMA', 'ucviz_xcat_gold')}"
else:
    B, S, G = f"{BRONZE}.{SCH}", f"{SILVER}.{SCH}", f"{GOLD}.{SCH}"


def _catalog_ddl(cat: str, layer: str) -> tuple[str, str]:
    loc = f" MANAGED LOCATION '{LOCATION}'" if LOCATION else ""
    return (f"CREATE CATALOG IF NOT EXISTS {cat}{loc} "
            f"COMMENT 'UCViz demo — {layer} layer (catalog-per-layer)'",
            f"catalog: {cat}")


STATEMENTS = ([] if REUSE else [
    # --- Catalogs (one per medallion layer) --------------------------------
    _catalog_ddl(BRONZE, "bronze"),
    _catalog_ddl(SILVER, "silver"),
    _catalog_ddl(GOLD, "gold"),
]) + [
    # --- Schemas ------------------------------------------------------------
    (f"CREATE SCHEMA IF NOT EXISTS {B} COMMENT 'Raw ingested sales data'",
     f"schema: {B}"),
    (f"CREATE SCHEMA IF NOT EXISTS {S} COMMENT 'Cleaned, conformed sales data'",
     f"schema: {S}"),
    (f"CREATE SCHEMA IF NOT EXISTS {G} COMMENT 'Consumer-facing data products'",
     f"schema: {G}"),

    # --- Bronze: raw synthetic data ---------------------------------------
    (f"""CREATE OR REPLACE TABLE {B}.raw_customers
      COMMENT 'Raw CRM customer extract' AS
      SELECT id AS customer_id,
        element_at(array('Ava','Liam','Noah','Emma','Olivia','Sophia','Mateo','Lucas','Mia','Ethan'), cast(pmod(id,10)+1 AS int)) AS first_name,
        element_at(array('Kim','Patel','Garcia','Chen','Nguyen','Silva','Muller','Haddad','Rossi','Okafor'), cast(pmod(id*7,10)+1 AS int)) AS last_name,
        concat('user', id, '@example.com') AS email,
        concat('+1-555-', lpad(cast(pmod(id*13,10000) AS string),4,'0')) AS phone,
        element_at(array('North','South','East','West','Central'), cast(pmod(id,5)+1 AS int)) AS region
      FROM (SELECT explode(sequence(1,500)) AS id)""",
     "bronze: raw_customers"),
    (f"""CREATE OR REPLACE TABLE {B}.raw_products
      COMMENT 'Raw product master' AS
      SELECT id AS product_id,
        element_at(array('NAND Flash 1TB','SSD 2TB','microSD 256GB','USB 128GB','HDD 8TB'), cast(id AS int)) AS product_name,
        element_at(array('Flash','SSD','Card','USB','HDD'), cast(id AS int)) AS category
      FROM (SELECT explode(sequence(1,5)) AS id)""",
     "bronze: raw_products"),
    (f"""CREATE OR REPLACE TABLE {B}.raw_orders
      COMMENT 'Raw order events' AS
      SELECT id AS order_id,
        cast(pmod(id*13,500)+1 AS int) AS customer_id,
        cast(pmod(id*3,5)+1 AS int) AS product_id,
        date_add(DATE'2026-01-01', cast(pmod(id,300) AS int)) AS order_date,
        round(rand(42)*490+10, 2) AS amount,
        element_at(array('PLACED','SHIPPED','DELIVERED','CANCELLED','RETURNED'), cast(pmod(id,5)+1 AS int)) AS status
      FROM (SELECT explode(sequence(1,2000)) AS id)""",
     "bronze: raw_orders"),
    (f"""CREATE OR REPLACE TABLE {B}.raw_orders_late
      COMMENT 'Late-arriving orders (appended to silver via INSERT)' AS
      SELECT id + 100000 AS order_id,
        cast(pmod(id*7,500)+1 AS int) AS customer_id,
        cast(pmod(id,5)+1 AS int) AS product_id,
        date_add(DATE'2026-10-01', cast(pmod(id,30) AS int)) AS order_date,
        round(rand(7)*300+20, 2) AS amount, 'DELIVERED' AS status
      FROM (SELECT explode(sequence(1,100)) AS id)""",
     "bronze: raw_orders_late"),

    # --- Silver: cross-catalog CTAS from bronze ----------------------------
    (f"""CREATE OR REPLACE TABLE {S}.customers
      COMMENT 'Conformed customers' AS
      SELECT customer_id, initcap(first_name) AS first_name,
        initcap(last_name) AS last_name, lower(email) AS email, phone, region
      FROM {B}.raw_customers WHERE email IS NOT NULL""",
     "silver: customers  <- bronze.raw_customers"),
    (f"""CREATE OR REPLACE TABLE {S}.products
      COMMENT 'Conformed products' AS
      SELECT product_id, product_name, category FROM {B}.raw_products""",
     "silver: products   <- bronze.raw_products"),
    (f"""CREATE OR REPLACE TABLE {S}.orders
      COMMENT 'Valid orders enriched with customer region' AS
      SELECT o.order_id, o.customer_id, o.product_id, o.order_date, o.amount,
        o.status, c.region
      FROM {B}.raw_orders o JOIN {S}.customers c ON o.customer_id = c.customer_id
      WHERE o.status <> 'CANCELLED'""",
     "silver: orders     <- bronze.raw_orders + silver.customers"),
    (f"""INSERT INTO {S}.orders
      SELECT l.order_id, l.customer_id, l.product_id, l.order_date, l.amount,
        l.status, c.region
      FROM {B}.raw_orders_late l JOIN {S}.customers c
        ON l.customer_id = c.customer_id""",
     "silver: orders     <- INSERT from bronze.raw_orders_late"),

    # --- Gold: cross-catalog CTAS from silver ------------------------------
    (f"""CREATE OR REPLACE TABLE {G}.customer_360
      COMMENT 'One row per customer with lifetime value' AS
      SELECT c.customer_id, c.first_name, c.last_name, c.region,
        count(o.order_id) AS lifetime_orders,
        round(coalesce(sum(o.amount),0),2) AS lifetime_value,
        max(o.order_date) AS last_order_date
      FROM {S}.customers c LEFT JOIN {S}.orders o ON c.customer_id = o.customer_id
      GROUP BY c.customer_id, c.first_name, c.last_name, c.region""",
     "gold: customer_360  <- silver.customers + silver.orders"),
    (f"""CREATE OR REPLACE TABLE {G}.sales_by_region
      COMMENT 'Monthly sales by region and product category' AS
      SELECT o.region, p.category, date_trunc('MONTH', o.order_date) AS sales_month,
        count(*) AS order_count, round(sum(o.amount),2) AS total_sales
      FROM {S}.orders o JOIN {S}.products p ON o.product_id = p.product_id
      GROUP BY o.region, p.category, date_trunc('MONTH', o.order_date)""",
     "gold: sales_by_region <- silver.orders + silver.products"),

    # --- Tags (fail harmlessly if your tag policies don't allow them) -------
    (f"ALTER TABLE {B}.raw_customers SET TAGS ('business_domain'='Commercial','classification'='restricted')", "tag: bronze.raw_customers"),
    (f"ALTER TABLE {B}.raw_orders SET TAGS ('business_domain'='Supply Chain','classification'='confidential')", "tag: bronze.raw_orders"),
    (f"ALTER TABLE {S}.customers SET TAGS ('business_domain'='Commercial','classification'='restricted')", "tag: silver.customers"),
    (f"ALTER TABLE {S}.orders SET TAGS ('business_domain'='Supply Chain','classification'='confidential')", "tag: silver.orders"),
    (f"ALTER TABLE {G}.customer_360 SET TAGS ('business_domain'='Commercial','classification'='restricted')", "tag: gold.customer_360"),
    (f"ALTER TABLE {G}.sales_by_region SET TAGS ('business_domain'='Supply Chain','classification'='public')", "tag: gold.sales_by_region"),
    # Silver email/phone classified; bronze email/phone deliberately left
    # untagged so Table Health flags "unclassified sensitive data".
    (f"ALTER TABLE {S}.customers ALTER COLUMN email SET TAGS ('classification'='restricted')", "coltag: silver.customers.email"),
    (f"ALTER TABLE {S}.customers ALTER COLUMN phone SET TAGS ('classification'='restricted')", "coltag: silver.customers.phone"),

    # --- Column mask on silver email (phone stays classified-but-unmasked) -
    (f"""CREATE OR REPLACE FUNCTION {S}.mask_email(v STRING)
      RETURN CASE WHEN is_account_group_member('admins') THEN v
             ELSE regexp_replace(v, '(^.).*(@.*$)', '$1***$2') END""",
     "func: silver.mask_email"),
    (f"ALTER TABLE {S}.customers ALTER COLUMN email SET MASK {S}.mask_email",
     "mask: silver.customers.email"),

    # --- Grants: engineers = bronze (catalog level) + silver (schema level)
    ((f"GRANT USE CATALOG ON CATALOG {BRONZE} TO `{ENGINEERS}`",
      f"grant: {ENGINEERS} USE CATALOG {BRONZE}") if REUSE else
     (f"GRANT USE CATALOG, USE SCHEMA, SELECT ON CATALOG {BRONZE} TO `{ENGINEERS}`",
      f"grant: {ENGINEERS} catalog-level on {BRONZE}")),
] + ([
    (f"GRANT USE SCHEMA, SELECT ON SCHEMA {B} TO `{ENGINEERS}`",
     f"grant: {ENGINEERS} schema-level on {B}"),
] if REUSE else []) + [
    (f"GRANT USE CATALOG ON CATALOG {SILVER} TO `{ENGINEERS}`",
     f"grant: {ENGINEERS} USE CATALOG {SILVER}"),
    (f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA {S} TO `{ENGINEERS}`",
     f"grant: {ENGINEERS} schema-level on {S}"),
    # --- Grants: analysts = gold (table level) + one silver table ----------
    (f"GRANT USE CATALOG ON CATALOG {GOLD} TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} USE CATALOG {GOLD}"),
    (f"GRANT USE SCHEMA ON SCHEMA {G} TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} USE SCHEMA {G}"),
    (f"GRANT SELECT ON TABLE {G}.customer_360 TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} SELECT {G}.customer_360"),
    (f"GRANT USE CATALOG ON CATALOG {SILVER} TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} USE CATALOG {SILVER}"),
    (f"GRANT USE SCHEMA ON SCHEMA {S} TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} USE SCHEMA {S}"),
    (f"GRANT SELECT ON TABLE {S}.orders TO `{ANALYSTS}`",
     f"grant: {ANALYSTS} SELECT {S}.orders"),
    # --- Broad grant (over-permission finding) -----------------------------
    (f"GRANT SELECT ON TABLE {G}.sales_by_region TO `account users`",
     "grant: account users SELECT gold.sales_by_region (broad)"),
] + ([
    # --- App SP, reuse mode: read on the demo schemas only ------------------
    (f"GRANT USE CATALOG ON CATALOG {cat} TO `{APP_SP}`",
     f"grant: app SP USE CATALOG {cat}")
    for cat in sorted({BRONZE, SILVER, GOLD})
] + [
    (f"GRANT USE SCHEMA, SELECT ON SCHEMA {sch} TO `{APP_SP}`",
     f"grant: app SP read on {sch}")
    for sch in (B, S, G)
] if REUSE else [
    # --- App service principal: read + browse on all three catalogs so the
    #     real-time lineage API resolves every hop -------------------------
    (f"GRANT USE CATALOG, USE SCHEMA, SELECT, BROWSE ON CATALOG {cat} TO `{APP_SP}`",
     f"grant: app SP on {cat}")
    for cat in (BRONZE, SILVER, GOLD)
]) + ([
    # MANAGE lets the SP see every principal's grants in information_schema.
    (f"GRANT MANAGE ON SCHEMA {sch} TO `{APP_SP}`", f"grant: app SP MANAGE {sch}")
    for sch in (B, S, G)
] if SP_MANAGE else [])

TEARDOWN = ([(f"DROP SCHEMA IF EXISTS {sch} CASCADE", f"drop: {sch}")
             for sch in (G, S, B)] if REUSE else
            [(f"DROP CATALOG IF EXISTS {cat} CASCADE", f"drop: {cat}")
             for cat in (GOLD, SILVER, BRONZE)])


def ensure_groups() -> None:
    """Create the test groups in the workspace if they don't exist yet.

    Needs workspace-admin rights. UC grants require *account* groups; on
    identity-federated workspaces groups created here are account groups, but
    if a GRANT later fails with a workspace-local-group error, create the groups
    in the account console (or point UCVIZ_TEST_GROUPS at existing ones).
    """
    for name in (ENGINEERS, ANALYSTS):
        flt = f'displayName eq "{name}"'
        found = _api("GET", "/api/2.0/preview/scim/v2/Groups?filter="
                     + flt.replace(" ", "%20").replace('"', "%22"))
        if "_error" not in found and found.get("totalResults", 0) > 0:
            print(f"[OK ] group exists: {name}")
            continue
        made = _api("POST", "/api/2.0/preview/scim/v2/Groups", {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": name,
        })
        if "_error" in made:
            print(f"[FAIL] create group {name}  -> {made['_error'][:200]}")
        else:
            print(f"[OK ] created group: {name}")


def execute(statements) -> int:
    ok = 0
    for stmt, label in statements:
        state, detail = run(stmt)
        mark = "OK " if state == "SUCCEEDED" else "FAIL"
        print(f"[{mark}] {label}" + (f"  -> {detail}" if detail else ""))
        ok += state == "SUCCEEDED"
    print(f"\n{ok}/{len(statements)} statements succeeded.")
    return 0 if ok == len(statements) else 1


def main(argv) -> int:
    print(f"Layers: {B} -> {S} -> {G}")
    if "--teardown" in argv:
        what = "SCHEMA" if REUSE else "CATALOG"
        targets = (G, S, B) if REUSE else (GOLD, SILVER, BRONZE)
        answer = input(f"DROP {what} ... CASCADE on {', '.join(targets)}? "
                       "Type 'drop' to confirm: ")
        if answer.strip() != "drop":
            print("Aborted.")
            return 1
        return execute(TEARDOWN)

    if not SKIP_GROUPS:
        ensure_groups()
    rc = execute(STATEMENTS)
    print(json.dumps({
        "pick_in_ucviz": f"{G}.customer_360",
        "expect_upstream": [f"{S}.customers", f"{S}.orders",
                            f"{B}.raw_customers", f"{B}.raw_orders",
                            f"{B}.raw_orders_late"],
        "principal_access_demo": [ENGINEERS, ANALYSTS, "account users"],
    }, indent=2))
    print("\nNote: system.access.table_lineage can take up to ~1 hour to show "
          "these edges; UCViz's real-time API path shows them sooner.")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
