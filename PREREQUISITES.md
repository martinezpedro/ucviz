# UCViz — Prerequisites

Everything required for the Unity Catalog Visualizer to run and produce meaningful
output. UCViz is **read-only**: it queries system / `information_schema` tables through a
SQL warehouse and the lineage-tracking REST API — it never writes data.

---

## 1. Platform

- **Databricks workspace with Unity Catalog enabled** (the whole app is UC-centric).
- **Databricks Apps enabled** on the workspace (UCViz is deployed as a Databricks App).
- **A running SQL warehouse** (serverless or pro) — all metadata queries run through it.
- **UC system tables**: the **`system.access`** schema must be enabled in the account
  (system schemas are opt-in) for the SQL lineage path and the Admin → Connection Test.
  The real-time graph uses the lineage REST API and works even before `system.access` is on.
- **UC lineage capture** (on by default) — the graph only shows lineage that UC actually
  recorded (e.g. CTAS, jobs, queries).

## 2. App deployment

- Source synced to the workspace and the app created / deployed
  (`databricks sync …` + `databricks apps deploy <name> --source-code-path …`).
- **A SQL warehouse bound as an app resource named `sql-warehouse-id`** — `app.yaml` reads
  `DATABRICKS_WAREHOUSE_ID` via `valueFrom: sql-warehouse-id`. Without this the app raises
  "DATABRICKS_WAREHOUSE_ID is not set".
- **User authorization with the `sql` scope** must stay enabled on the app. With the default
  (`query_identity: app`) data queries ignore the viewer's token, but the **Admin-page group
  check** runs `is_member()` / `is_account_group_member()` as the viewer. (With
  `query_identity: viewer` the token also runs every query.)
- **Python dependencies** (`requirements.txt`), installed at build time:
  `databricks-sdk>=0.29`, `databricks-sql-connector>=3.1`, `streamlit>=1.37`
  (needed for `st.fragment` / `st.rerun(scope=…)`), `streamlit-extras>=0.4`,
  `pandas>=2.0`, `networkx>=3.1`, `plotly>=5.22`, **`streamlit-flow-component>=1.6`**
  (the React-Flow lineage graph), `pyyaml>=6.0`.

## 3. Identity & auth model

- **App service principal (default, `query_identity: app`)** — created automatically with
  the app. It runs **all** SQL (pickers, lineage trace, Access, Catalog & Schema Grants, Tags,
  Table Health, Sensitive Data, Principal Access) plus the **lineage-tracking REST API** and
  **SCIM** lookups. Results reflect what the *SP* can see, for every viewer.
- **Who can use the app** is therefore the access control: grant `CAN_USE` on the app only to
  the dedicated admin group. Every member sees everything the SP can see until v2 per-group
  scoping exists (README, "v2 — per-group catalog scoping").
- *(Alternative)* `query_identity: viewer` runs SQL on-behalf-of-user instead; then each
  viewer needs the section 5 grants themselves.

## 4. Permissions — the end user (viewer)

- **`CAN_USE`** on the app — nothing else (no catalog, warehouse or system-table grants).

## 5. Permissions — the app service principal

```sql
-- system tables (system.access grants must be made by an account/metastore admin)
GRANT USE CATALOG ON CATALOG system TO `<app-sp-application-id>`;
GRANT USE SCHEMA  ON SCHEMA  system.access TO `<app-sp-application-id>`;
GRANT SELECT ON TABLE system.access.table_lineage  TO `<app-sp-application-id>`;
GRANT SELECT ON TABLE system.access.column_lineage TO `<app-sp-application-id>`;
GRANT USE SCHEMA  ON SCHEMA  system.information_schema TO `<app-sp-application-id>`;
-- metadata visibility on every catalog UCViz should cover (e.g. bronze/silver/gold):
GRANT BROWSE, USE CATALOG, USE SCHEMA ON CATALOG <catalog> TO `<app-sp-application-id>`;
-- REQUIRED to see *other principals'* grants (Access tab, Catalog & Schema Grants,
-- Principal Access). Grant at CATALOG level so catalog grants are visible too:
GRANT MANAGE ON CATALOG <catalog> TO `<app-sp-application-id>`;
```

- **Why `MANAGE`:** `information_schema` privilege views only show a grant to the caller if
  the caller is the grantee, owns the object, or holds `MANAGE` on it. Verified in testing: with only `BROWSE`/`USE`/`SELECT` the SP saw just its own grants and owners;
  with `MANAGE` on a schema it saw every schema- and table-level grant, but catalog-level
  grants stayed hidden until `MANAGE` is on the catalog. `MANAGE` also allows granting and
  revoking — UCViz never does either (read-only), but the app's identity *can*, so restrict
  who can deploy/modify the app. Alternatives: make the SP the owner, or a metastore admin.

- `BROWSE` lets the SP read metadata (information_schema rows, lineage API results) without
  reading data — UCViz never reads table contents. Without visibility, a catalog's objects
  are missing from pickers, grants, Principal Access and the lineage graph.
- **`CAN_USE` on the SQL warehouse** — granted automatically by binding it as the
  `sql-warehouse-id` app resource.
- **Workspace-catalog bindings:** information_schema only returns catalogs bound to the
  app's workspace. Catalogs bound elsewhere need a (read-only) binding to it — to validate.
- **SCIM:** listing groups and service principals works for a regular SP (labels grantees and
  shows SP display names). Reading a **user's group memberships** (Principal Access →
  "Include access through group membership") requires the SP to be a **workspace admin** —
  verified in testing: as a non-admin it got no groups; after adding it to `admins` it resolved
  them. Add the SP to the workspace `admins` group (INSTALL.md step 6). Without it the page
  says so and shows direct access only.

## 6. Admin page access

- The **Admin** page is gated on the signed-in user's **direct SCIM groups** (looked up by the
  app SP from the forwarded email) matching `admin_groups` in the config (default:
  `account admins`, `metastore_admins`, `admins` = workspace admins). Set your own UCViz admin
  group at a customer. This is **separate** from the app's `CAN_MANAGE` permission.
- The check runs in SQL as the signed-in user (`is_member` for workspace groups such as
  `admins`, `is_account_group_member` for account groups), so it needs no SCIM rights.
- It **fails closed**: if membership can't be checked, Admin is denied.
- Admin edits are written to the running app's copy of `config/app_config.yaml`; a redeploy
  replaces it with the repo version. Commit config changes to the repo.
- **External Sources** registration (writes UC External Metadata / external lineage as the SP)
  is hidden unless `enable_external_source_writes: true` — keep it off for a read-only install.

## 7. Configuration — `config/app_config.yaml` (editable in the Admin page)

- `catalogs` — the scan scope (empty = every catalog the caller can see). **Set it at a
  customer**: with `query_identity: app` an empty list shows every catalog the SP can browse
  (on a shared metastore that can include other teams' catalogs via `account users` BROWSE).
- `max_hops` — lineage depth bound.
- `max_graph_tables` — a whole-schema pick larger than this must be narrowed before graphing.
- `layer_rules` — substring → medallion layer (EXTERNAL/BRONZE/SILVER/GOLD/CONSUMER).
- `tag_roles` — which tag keys mean domain / sensitivity / abac (drives pill coloring).
- `high_sensitivity_values` — tag values rendered as high-severity (red).
- `admin_groups` — groups allowed into the Admin page.

If the deployed filesystem is read-only, Admin edits live in session state and can be
downloaded as YAML to redeploy.

## 8. What each panel needs to show something

- **Lineage graph** — UC lineage captured for the table (REST API returns upstream/downstream).
- **Access** — table/schema/catalog grants and/or an owner on the tables.
- **Tags & Table Health** — table/column tags, column masks (CLS), row filters (RLS).
- **Sensitive Data** — nothing extra; it's a heuristic scan of column *names*.

## Notes

- **Tag policies** are only relevant when *seeding* demo tags (`scripts/seed_cross_catalog_demo.py`):
  a workspace may restrict allowed tag values (governed tags). UCViz only *reads* tags at
  runtime, so this is not a runtime prerequisite.
- **Python** in the Apps runtime is 3.11; the code uses `from __future__ import annotations`
  so it also byte-compiles under 3.9.

## 9. Performance (measured on a large shared metastore, Serverless Starter warehouse)

- Full cross-catalog trace of a 6-table, 3-catalog-hop lineage: ~26 s cold, <2 s cached
  (results cache for 5 min). Whole-catalog trace of 50 tables: ~45 s. Principal Access for one
  principal: ~5–40 s depending on how many grants it holds.
- The cost is dominated by `system.access.table_lineage` (~3 s per hop on a large shared
  metastore) plus per-schema metadata queries. Before a demo, open the demo tables once to
  warm the cache.

