# Databricks notebook source
# MAGIC %md
# MAGIC # UCViz installer
# MAGIC
# MAGIC Installs (or updates) the **UCViz** Databricks App in this workspace — no CLI needed.
# MAGIC
# MAGIC **How to use**
# MAGIC 1. Fill in the boxes at the top of this page (they appear after the first run of the next cell, or click **Run all** once).
# MAGIC 2. Click **Run all**.
# MAGIC 3. Read the summary at the bottom: the app URL, what was done, and — if anything needs someone with more rights — the exact SQL to send them.
# MAGIC
# MAGIC Re-run this notebook any time to **update** the app after pulling a new version of the Git folder (top right: *Pull*). Set **action** to `uninstall` to remove it.
# MAGIC
# MAGIC **Who should run it:** a **workspace admin**. The two system-table grants also need an **account or metastore admin**; if you aren't one, the summary lists the SQL to send to one. Catalog grants need the **catalog owner** (or a metastore admin).
# MAGIC
# MAGIC **Compute:** any Unity Catalog compute — **Serverless** is easiest.
# MAGIC
# MAGIC | Box | What to enter |
# MAGIC |---|---|
# MAGIC | `catalogs` | Comma-separated catalogs UCViz should cover, e.g. `bronze_cat, silver_cat, gold_cat` (**required**) |
# MAGIC | `admin_group` | Group whose members can open UCViz's Admin page |
# MAGIC | `users_group` | Group allowed to use the app (gets *Can Use*) |
# MAGIC | `warehouse` | SQL warehouse **name or ID**. Leave empty to use the first serverless warehouse |
# MAGIC | `app_name` | Name of the app (default `ucviz`) |
# MAGIC | `grant_manage` | `yes` = grant MANAGE on the catalogs so the app can see everyone's grants (recommended; needs security sign-off) |
# MAGIC | `make_sp_workspace_admin` | `yes` = add the app's service principal to the workspace `admins` group so it can read users' group memberships |
# MAGIC | `action` | `install` (also updates) or `uninstall` |
# MAGIC | `create_demo_data` | `yes` = also build a small synthetic bronze → silver → gold demo (catalogs `ucviz_demo_bronze/_silver/_gold`) with lineage, tags and grants, and add it to the covered catalogs. Handy for a test or Free Edition workspace. With `uninstall`, `yes` also removes it. |

# COMMAND ----------

# MAGIC %pip install --quiet --upgrade databricks-sdk pyyaml

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalogs", "", "1. Catalogs (comma-separated)")
dbutils.widgets.text("admin_group", "", "2. Admin group")
dbutils.widgets.text("users_group", "", "3. Users group (Can Use)")
dbutils.widgets.text("warehouse", "", "4. SQL warehouse name or ID")
dbutils.widgets.text("app_name", "ucviz", "5. App name")
dbutils.widgets.dropdown("grant_manage", "yes", ["yes", "no"], "6. Grant MANAGE on catalogs")
dbutils.widgets.dropdown("make_sp_workspace_admin", "yes", ["yes", "no"], "7. Make app SP workspace admin")
dbutils.widgets.dropdown("action", "install", ["install", "uninstall"], "8. Action")
dbutils.widgets.dropdown("create_demo_data", "no", ["no", "yes"], "9. Create demo data")

# COMMAND ----------

import io
import os
import re

import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service import apps as A
from databricks.sdk.service import iam
from databricks.sdk.service.workspace import ImportFormat

w = WorkspaceClient()
me = w.current_user.me()

CATALOGS = [c.strip() for c in dbutils.widgets.get("catalogs").split(",") if c.strip()]
ADMIN_GROUP = dbutils.widgets.get("admin_group").strip()
USERS_GROUP = dbutils.widgets.get("users_group").strip()
WAREHOUSE = dbutils.widgets.get("warehouse").strip()
APP_NAME = dbutils.widgets.get("app_name").strip() or "ucviz"
GRANT_MANAGE = dbutils.widgets.get("grant_manage") == "yes"
MAKE_ADMIN = dbutils.widgets.get("make_sp_workspace_admin") == "yes"
ACTION = dbutils.widgets.get("action")
DEMO = dbutils.widgets.get("create_demo_data") == "yes"
DEMO_CATALOGS = ["ucviz_demo_bronze", "ucviz_demo_silver", "ucviz_demo_gold"]
DEMO_SCHEMAS = ["ucviz_demo_bronze", "ucviz_demo_silver", "ucviz_demo_gold"]

# Where the Git folder (this repo) lives, and where the app's source is deployed
# from. The deploy folder is a copy with the generated config, so the Git folder
# stays clean and can always be pulled.
_nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
_nb = _nb if _nb.startswith("/Workspace") else "/Workspace" + _nb
REPO_ROOT = os.path.dirname(os.path.dirname(_nb))
DEPLOY_DIR = f"/Workspace/Shared/{APP_NAME}_app"

results = []    # (step, status, detail)
manual_sql = [] # grants the runner couldn't make, to forward to an admin


def step(name, status, detail=""):
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


if not re.fullmatch(r"[a-z0-9-]{2,30}", APP_NAME):
    raise ValueError("app_name: 2–30 characters, lowercase letters, numbers and '-' only.")
if ACTION == "install" and not CATALOGS and not DEMO:
    raise ValueError("Fill in 'catalogs' (top of the page) with the catalogs UCViz should "
                     "cover, or set 'create_demo_data' to yes.")
print(f"Running as {me.user_name}\nRepo: {REPO_ROOT}\nDeploy folder: {DEPLOY_DIR}\nApp: {APP_NAME}")

# COMMAND ----------

# MAGIC %md ## Uninstall (only when action = uninstall)

# COMMAND ----------

def drop_demo():
    """Remove the demo data in either layout (own catalogs, or schemas)."""
    for cat in DEMO_CATALOGS:
        try:
            spark.sql(f"DROP CATALOG IF EXISTS `{cat}` CASCADE")
        except Exception:
            pass
    fallback = CATALOGS[0] if CATALOGS else spark.sql("SELECT current_catalog()").first()[0]
    for sch in DEMO_SCHEMAS:
        try:
            spark.sql(f"DROP SCHEMA IF EXISTS `{fallback}`.`{sch}` CASCADE")
        except Exception:
            pass


if ACTION == "uninstall":
    if DEMO:
        drop_demo()
        step("Remove demo data", "OK")
    try:
        w.apps.delete(APP_NAME)
        step("Delete app", "OK", APP_NAME)
    except Exception as e:
        step("Delete app", "SKIPPED", str(e)[:200])
    try:
        w.workspace.delete(DEPLOY_DIR, recursive=True)
        step("Delete deploy folder", "OK", DEPLOY_DIR)
    except Exception as e:
        step("Delete deploy folder", "SKIPPED", str(e)[:200])
    print("\nThe app's service principal is removed with the app; its grants go with it.")
    dbutils.notebook.exit("uninstalled")

# COMMAND ----------

# MAGIC %md ## 0. Demo data (only when create_demo_data = yes)

# COMMAND ----------

def build_demo():
    """Synthetic bronze -> silver -> gold demo with cross-layer lineage.

    Tries one catalog per layer first; if the workspace can't create catalogs,
    falls back to three schemas inside an existing catalog. Returns the
    catalogs UCViz should cover for it.
    """
    try:
        for cat in DEMO_CATALOGS:
            spark.sql(f"CREATE CATALOG IF NOT EXISTS `{cat}` "
                      "COMMENT 'UCViz demo data (synthetic) — safe to delete'")
        layers = [f"`{c}`.`sales`" for c in DEMO_CATALOGS]
        for sch in layers:
            spark.sql(f"CREATE SCHEMA IF NOT EXISTS {sch}")
        covered = list(DEMO_CATALOGS)
        layout = "catalogs " + ", ".join(DEMO_CATALOGS)
    except Exception as e:
        fallback = CATALOGS[0] if CATALOGS else spark.sql("SELECT current_catalog()").first()[0]
        print(f"Can't create catalogs here ({str(e).splitlines()[0][:120]}) — "
              f"using schemas in catalog '{fallback}' instead.")
        layers = [f"`{fallback}`.`{s}`" for s in DEMO_SCHEMAS]
        for sch in layers:
            spark.sql(f"CREATE SCHEMA IF NOT EXISTS {sch} "
                      "COMMENT 'UCViz demo data (synthetic) — safe to delete'")
        covered = [fallback]
        layout = f"schemas {', '.join(DEMO_SCHEMAS)} in {fallback}"
    B, S, G = layers

    core = [
        f"""CREATE OR REPLACE TABLE {B}.raw_customers COMMENT 'Raw CRM customer extract' AS
        SELECT id AS customer_id,
          element_at(array('Ava','Liam','Noah','Emma','Olivia','Sophia','Mateo','Lucas','Mia','Ethan'), cast(pmod(id,10)+1 AS int)) AS first_name,
          element_at(array('Kim','Patel','Garcia','Chen','Nguyen','Silva','Muller','Haddad','Rossi','Okafor'), cast(pmod(id*7,10)+1 AS int)) AS last_name,
          concat('user', id, '@example.com') AS email,
          concat('+1-555-', lpad(cast(pmod(id*13,10000) AS string),4,'0')) AS phone,
          element_at(array('North','South','East','West','Central'), cast(pmod(id,5)+1 AS int)) AS region
        FROM (SELECT explode(sequence(1,500)) AS id)""",
        f"""CREATE OR REPLACE TABLE {B}.raw_products COMMENT 'Raw product master' AS
        SELECT id AS product_id,
          element_at(array('Flash 1TB','SSD 2TB','Card 256GB','USB 128GB','HDD 8TB'), cast(id AS int)) AS product_name,
          element_at(array('Flash','SSD','Card','USB','HDD'), cast(id AS int)) AS category
        FROM (SELECT explode(sequence(1,5)) AS id)""",
        f"""CREATE OR REPLACE TABLE {B}.raw_orders COMMENT 'Raw order events' AS
        SELECT id AS order_id, cast(pmod(id*13,500)+1 AS int) AS customer_id,
          cast(pmod(id*3,5)+1 AS int) AS product_id,
          date_add(DATE'2026-01-01', cast(pmod(id,300) AS int)) AS order_date,
          round(rand(42)*490+10, 2) AS amount,
          element_at(array('PLACED','SHIPPED','DELIVERED','CANCELLED','RETURNED'), cast(pmod(id,5)+1 AS int)) AS status
        FROM (SELECT explode(sequence(1,2000)) AS id)""",
        f"""CREATE OR REPLACE TABLE {S}.customers COMMENT 'Conformed customers' AS
        SELECT customer_id, initcap(first_name) AS first_name, initcap(last_name) AS last_name,
          lower(email) AS email, phone, region FROM {B}.raw_customers""",
        f"""CREATE OR REPLACE TABLE {S}.products COMMENT 'Conformed products' AS
        SELECT product_id, product_name, category FROM {B}.raw_products""",
        f"""CREATE OR REPLACE TABLE {S}.orders COMMENT 'Valid orders with customer region' AS
        SELECT o.order_id, o.customer_id, o.product_id, o.order_date, o.amount, o.status, c.region
        FROM {B}.raw_orders o JOIN {S}.customers c ON o.customer_id = c.customer_id
        WHERE o.status <> 'CANCELLED'""",
        f"""CREATE OR REPLACE TABLE {G}.customer_360 COMMENT 'One row per customer with lifetime value' AS
        SELECT c.customer_id, c.first_name, c.last_name, c.region,
          count(o.order_id) AS lifetime_orders, round(coalesce(sum(o.amount),0),2) AS lifetime_value
        FROM {S}.customers c LEFT JOIN {S}.orders o ON c.customer_id = o.customer_id
        GROUP BY c.customer_id, c.first_name, c.last_name, c.region""",
        f"""CREATE OR REPLACE TABLE {G}.sales_by_region COMMENT 'Monthly sales by region and category' AS
        SELECT o.region, p.category, date_trunc('MONTH', o.order_date) AS sales_month,
          count(*) AS order_count, round(sum(o.amount),2) AS total_sales
        FROM {S}.orders o JOIN {S}.products p ON o.product_id = p.product_id
        GROUP BY o.region, p.category, date_trunc('MONTH', o.order_date)""",
    ]
    for sql in core:
        spark.sql(sql)
    # Optional extras — skipped quietly if tag policies or permissions refuse them.
    extras = [
        f"ALTER TABLE {S}.customers SET TAGS ('classification'='restricted')",
        f"ALTER TABLE {G}.customer_360 SET TAGS ('classification'='restricted')",
        f"ALTER TABLE {G}.sales_by_region SET TAGS ('classification'='public')",
        f"ALTER TABLE {S}.customers ALTER COLUMN email SET TAGS ('classification'='restricted')",
        f"""CREATE OR REPLACE FUNCTION {S}.mask_email(v STRING)
            RETURN CASE WHEN is_account_group_member('admins') THEN v
                   ELSE regexp_replace(v, '(^.).*(@.*$)', '$1***$2') END""",
        f"ALTER TABLE {S}.customers ALTER COLUMN email SET MASK {S}.mask_email",
        f"GRANT SELECT ON TABLE {G}.sales_by_region TO `account users`",
    ]
    skipped = 0
    for sql in extras:
        try:
            spark.sql(sql)
        except Exception:
            skipped += 1
    step("Demo data", "OK", f"8 tables in {layout}"
         + (f" ({skipped} optional tag/mask/grant statements skipped)" if skipped else ""))
    return covered


if ACTION == "install" and DEMO:
    for cat in build_demo():
        if cat not in CATALOGS:
            CATALOGS.append(cat)
    print(f"Catalogs UCViz will cover: {CATALOGS}")

# COMMAND ----------

# MAGIC %md ## 1. SQL warehouse

# COMMAND ----------

def pick_warehouse(value):
    whs = list(w.warehouses.list())
    if value:
        for wh in whs:
            if value in (wh.id, wh.name):
                return wh
        raise ValueError(f"No SQL warehouse with name or ID '{value}'. Available: "
                         + ", ".join(f"{x.name} ({x.id})" for x in whs))
    serverless = [x for x in whs if x.enable_serverless_compute]
    if serverless or whs:
        return (serverless or whs)[0]
    raise ValueError("No SQL warehouse found — create one (SQL Warehouses → Create) and re-run.")


wh = pick_warehouse(WAREHOUSE)
step("SQL warehouse", "OK", f"{wh.name} ({wh.id})")

# COMMAND ----------

# MAGIC %md ## 2. Create (or update) the app

# COMMAND ----------

resource = A.AppResource(
    name="sql-warehouse-id",
    description="SQL warehouse UCViz queries system tables through.",
    sql_warehouse=A.AppResourceSqlWarehouse(
        id=wh.id, permission=A.AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE),
)
spec = A.App(
    name=APP_NAME,
    description="Unity Catalog lineage and access visualizer (read-only)",
    resources=[resource],
    user_api_scopes=["sql"],
)

try:
    w.apps.get(APP_NAME)
    exists = True
except NotFound:
    exists = False

if exists:
    w.apps.update(APP_NAME, app=spec)
    step("App", "OK", "already existed — warehouse and settings updated")
else:
    print("Creating the app (takes 2–5 minutes the first time)…")
    w.apps.create_and_wait(app=spec)
    step("App", "OK", "created")

app = w.apps.get(APP_NAME)
SP = app.service_principal_client_id
SP_NAME = app.service_principal_name
step("App service principal", "OK", f"{SP_NAME} ({SP})")

# COMMAND ----------

# MAGIC %md ## 3. Copy the app source and write its configuration

# COMMAND ----------

SKIP_DIRS = {".git", "install", "__pycache__", ".databricks"}

cfg_path = os.path.join(REPO_ROOT, "config", "app_config.yaml")
with open(cfg_path) as f:
    cfg_text = f.read()
cfg = yaml.safe_load(cfg_text) or {}
cfg["catalogs"] = CATALOGS
cfg["admin_groups"] = sorted({*(cfg.get("admin_groups") or []), *([ADMIN_GROUP] if ADMIN_GROUP else [])})
cfg["query_identity"] = "app"

try:
    w.workspace.delete(DEPLOY_DIR, recursive=True)
except Exception:
    pass  # first install

copied = 0
for root, dirs, files in os.walk(REPO_ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    rel = os.path.relpath(root, REPO_ROOT)
    target_dir = DEPLOY_DIR if rel == "." else f"{DEPLOY_DIR}/{rel}"
    w.workspace.mkdirs(target_dir)
    for name in files:
        src = os.path.join(root, name)
        if rel == "config" and name == "app_config.yaml":
            data = yaml.safe_dump(cfg, sort_keys=False).encode()
        else:
            with open(src, "rb") as f:
                data = f.read()
        w.workspace.upload(f"{target_dir}/{name}", io.BytesIO(data),
                           format=ImportFormat.RAW, overwrite=True)
        copied += 1

step("Copy app source", "OK", f"{copied} files → {DEPLOY_DIR}")
step("Configuration", "OK", f"catalogs={CATALOGS}, admin_groups={cfg['admin_groups']}")

# COMMAND ----------

# MAGIC %md ## 4. Grants for the app's service principal

# COMMAND ----------

def grant(sql, why, who):
    try:
        spark.sql(sql)
        step(f"GRANT {why}", "OK")
    except Exception as e:
        msg = str(e).split("\n")[0][:160]
        step(f"GRANT {why}", "NEEDS ADMIN", f"{who}: {msg}")
        manual_sql.append((who, sql + ";"))


sp = f"`{SP}`"
SYSTEM_WHO = "account admin or metastore admin"
for sql, why in [
    (f"GRANT USE CATALOG ON CATALOG system TO {sp}", "USE CATALOG system"),
    (f"GRANT USE SCHEMA ON SCHEMA system.access TO {sp}", "USE SCHEMA system.access"),
    (f"GRANT SELECT ON TABLE system.access.table_lineage TO {sp}", "SELECT table_lineage"),
    (f"GRANT SELECT ON TABLE system.access.column_lineage TO {sp}", "SELECT column_lineage"),
    (f"GRANT USE SCHEMA ON SCHEMA system.information_schema TO {sp}", "USE SCHEMA system.information_schema"),
]:
    grant(sql, why, SYSTEM_WHO)

for cat in CATALOGS:
    grant(f"GRANT BROWSE, USE CATALOG, USE SCHEMA ON CATALOG `{cat}` TO {sp}",
          f"BROWSE/USE on {cat}", f"owner of catalog {cat} or metastore admin")
    if GRANT_MANAGE:
        grant(f"GRANT MANAGE ON CATALOG `{cat}` TO {sp}",
              f"MANAGE on {cat}", f"owner of catalog {cat} or metastore admin")

# COMMAND ----------

# MAGIC %md ## 5. Workspace admin for the service principal (optional)

# COMMAND ----------

if MAKE_ADMIN:
    try:
        sp_obj = next(iter(w.service_principals.list(filter=f'applicationId eq "{SP}"')))
        admins = next(iter(w.groups.list(filter='displayName eq "admins"')))
        w.groups.patch(
            admins.id,
            operations=[iam.Patch(op=iam.PatchOp.ADD, value={"members": [{"value": sp_obj.id}]})],
            schemas=[iam.PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP],
        )
        step("Add app SP to workspace admins", "OK")
    except Exception as e:
        step("Add app SP to workspace admins", "NEEDS ADMIN",
             f"a workspace admin must add '{SP_NAME}' to the admins group: {str(e)[:160]}")
else:
    step("Add app SP to workspace admins", "SKIPPED",
         "Principal Access will show users' direct access only")

# COMMAND ----------

# MAGIC %md ## 6. Deploy

# COMMAND ----------

if (w.apps.get(APP_NAME).compute_status.state.value or "") not in ("ACTIVE",):
    print("Starting app compute…")
    w.apps.start_and_wait(APP_NAME)

print("Deploying (1–3 minutes)…")
dep = w.apps.deploy_and_wait(APP_NAME, app_deployment=A.AppDeployment(source_code_path=DEPLOY_DIR))
state = dep.status.state.value if dep.status and dep.status.state else "UNKNOWN"
step("Deploy", "OK" if state == "SUCCEEDED" else "FAILED",
     (dep.status.message or "") if dep.status else "")

# COMMAND ----------

# MAGIC %md ## 7. Who can use the app

# COMMAND ----------

acl = [A.AppAccessControlRequest(user_name=me.user_name,
                                 permission_level=A.AppPermissionLevel.CAN_MANAGE)]
if USERS_GROUP:
    acl.append(A.AppAccessControlRequest(group_name=USERS_GROUP,
                                         permission_level=A.AppPermissionLevel.CAN_USE))
if ADMIN_GROUP:
    acl.append(A.AppAccessControlRequest(group_name=ADMIN_GROUP,
                                         permission_level=A.AppPermissionLevel.CAN_MANAGE))
try:
    w.apps.update_permissions(APP_NAME, access_control_list=acl)
    step("App permissions", "OK",
         ", ".join(f"{a.group_name or a.user_name}={a.permission_level.value}" for a in acl))
except Exception as e:
    step("App permissions", "FAILED", str(e)[:200])

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

app = w.apps.get(APP_NAME)
rows = "".join(
    f"<tr><td>{s}</td><td style='color:{ {'OK':'#00a972','SKIPPED':'#8892b0'}.get(st,'#ff3621') };font-weight:600'>{st}</td><td>{d}</td></tr>"
    for s, st, d in results)
todo = ""
if manual_sql:
    by_who = {}
    for who, sql in manual_sql:
        by_who.setdefault(who, []).append(sql)
    todo = "<h3>Still to do — send this SQL to the right person</h3>" + "".join(
        f"<p><b>{who}</b> runs:</p><pre>{chr(10).join(sqls)}</pre>" for who, sqls in by_who.items())
    todo += ("<p>Then re-run this notebook (or just reload the app). If <b>Admin → "
             "Connection Test</b> in the app already shows <b>Granted</b> for every "
             "line, the access is already covered (for example by a grant to "
             "<code>account users</code>) and you can ignore these.</p>")
demo_hint = ("<p><b>Demo data:</b> in the app pick catalog <code>ucviz_demo_gold</code> (or the "
             "<code>ucviz_demo_gold</code> schema) → <code>customer_360</code>. Lineage can take a few "
             "minutes to appear the first time.</p>") if DEMO else ""
displayHTML(f"""
<h2>UCViz: {'ready' if not manual_sql else 'installed — a few grants still needed'}</h2>
{demo_hint}
<p><b>Open the app:</b> <a href="{app.url}" target="_blank">{app.url}</a></p>
<p>First check: in the app, <b>Settings → Admin → Connection Test</b> — every line should say <b>Granted</b>.
Users: <b>Help → User Guide</b> in the app.</p>
{todo}
<h3>What was done</h3>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>Step</th><th>Status</th><th>Detail</th></tr>{rows}</table>
""")
