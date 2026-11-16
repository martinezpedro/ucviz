# UCViz — Unity Catalog Visualizer (V1)

> **Installing?** Import this repo as a **Git folder**, open
> `install/UCViz_Installer`, fill in the boxes and **Run all** — see
> [INSTALL.md](INSTALL.md) (Quick install, plus a manual route and all
> prerequisites). **Using it?** Open
> **Help → User Guide** in the app ([docs/USER_GUIDE.md](docs/USER_GUIDE.md)).

A read-only Databricks App (Streamlit) that visualizes **lineage, effective
access, tags, and per-table governance health** for any Unity Catalog table by
querying system tables through a SQL warehouse. No data movement, no pipelines,
no new tables — UCViz is a visualization layer over metadata UC already maintains.

```
UCViz (Streamlit) → SQL Warehouse → Unity Catalog System Tables → Render
```

Everything is generic: catalogs, layers, groups, source systems, and tag roles
are all dynamic or admin-configured. Nothing is hardcoded to a customer.

## What it does

- Pick any table via a **catalog → schema → table cascade** (scoped queries;
  the metastore is far too large — verified at ~3.6k catalogs / 500k tables —
  for a single flat dropdown). **All catalogs** searches every catalog for a
  table by name; **All schemas** lists a whole catalog.
- **Cross-catalog lineage** (Full trace mode, default): a bounded BFS over
  `system.access.table_lineage` — metastore-wide, so a Gold table in a gold
  catalog traces back through the silver and bronze *catalogs* — merged with
  the real-time lineage API. Upstream (and optionally downstream), cycle-safe,
  max depth from the **Max Lineage Hops** slider (default 5), capped at 300
  nodes. Each node carries a stripe in its catalog's color, with a catalog
  legend. **Step-by-step** mode keeps the original click-to-expand behaviour.
- **Catalog & Schema Grants** tab: who was granted what on the selected
  catalog/schema itself (plus owners) — shown even with no table picked.
- **Principal Access** page (left nav): pick a group, user or service
  principal and drill Catalog → Schema → Table through everything it can reach
  — privilege, direct vs inherited, ownership, and (for users) which group the
  access comes through.
- Whole-schema mode is **gated for large schemas**: picking "★ All tables" on a
  schema with more than `max_graph_tables` (default 50) tables withholds the
  graph and requires narrowing by **layer, tag/domain value, table-name search,
  or group-with-access** (or drilling to a single table) — no silent truncation.
- Walk `system.access.table_lineage` both directions (iterative BFS, max _N_
  hops) plus the near-real-time lineage REST API — sources **and** consumers.
- Render a left-to-right **React-Flow** graph (Sources → Bronze → Silver →
  Gold → Consumers); **click any node** to focus the tabs on that one table.
- **Effective Access** per table: who can actually reach it — direct table
  grants, grants **inherited** from the schema/catalog (via
  `table_privileges.inherited_from`), and table/schema/catalog **owners** — each
  labeled by principal type (**user / group / service principal**, resolved via
  SCIM) and by where the access comes from.
- **Table Health**: one consolidated, severity-ranked list of governance
  warnings per table, mapped to Well-Architected pillars — unclassified PII,
  sensitive-but-unmasked columns, no tags/classification, no owner, missing
  docs, over-permissioned (broad) grants, and orphaned (no-access) tables.
- Tags, RLS row filters, CLS column masks, and (best-effort) ABAC as colored pills.
- Lucide icons throughout — no emojis.

## Layout

```
UC_Visualizer/                    # deploy root (== "ucviz/")
├── app.py                        # entrypoint: st.navigation page router
├── app.yaml                      # Databricks Apps config (warehouse resource)
├── install/
│   └── UCViz_Installer.py        # notebook installer: create, grant, deploy, update, uninstall
├── requirements.txt
├── views/                        # page files (loaded only via st.navigation in app.py)
│   ├── lineage_access.py         # Lineage & Access: lineage, permissions, tags, health
│   ├── principal_access.py       # Principal Access: principal -> catalogs/schemas/tables
│   ├── admin.py                  # Admin: catalog access, connection test, layer/tag config
│   └── help.py                   # Help: renders docs/USER_GUIDE.md (tabs + searchable Q&A)
├── docs/
│   └── USER_GUIDE.md             # end-user manual (purpose, how it works, limits, Q&A)
├── modules/
│   ├── db.py                     # SQL connection + config load/save + current_user
│   ├── lineage_traversal.py      # BFS lineage graph builder + layer classification
│   ├── permissions_lookup.py     # permission chain, gaps, catalog/schema grants
│   ├── principal_access.py       # reverse view: one principal's grants + owners
│   ├── principals.py             # SCIM principal types + user group lookup
│   ├── access_scope.py           # v2 extension point: per-group catalog scoping
│   ├── governance.py             # Table Health (WAF-mapped findings)
│   ├── pii_scan.py               # name-based sensitive-column scan
│   ├── lineage_api.py            # real-time lineage REST API (app SP)
│   ├── tags_lookup.py            # tags, classification, RLS, CLS, ABAC
│   ├── external_sources.py       # External Metadata / External Lineage (SDK)
│   ├── visualization.py          # streamlit-flow (React-Flow) graph elements
│   ├── icons.py                  # Lucide inline-SVG helper
│   └── ui.py                     # shared theme + pill/badge/stat renderers
├── config/
│   └── app_config.yaml           # admin-managed catalogs, layers, tag roles
├── scripts/
│   └── seed_cross_catalog_demo.py # optional synthetic catalog-per-layer demo
└── assets/
    └── logo.svg
```

## System tables read

| Table | Purpose |
|-------|---------|
| `system.access.table_lineage` | source → target lineage |
| `system.access.column_lineage` | column-level lineage (connection test) |
| `system.information_schema.catalog_privileges` / `schema_privileges` | catalog/schema grants (grants tab, Principal Access) |
| `system.information_schema.table_privileges` | one principal's table grants (Principal Access) |
| `system.information_schema.catalogs` / `schemata` / `tables` | owners, table picker search |
| `{catalog}.information_schema.table_privileges` | grants — direct **and** inherited (`inherited_from`) |
| `{catalog}.information_schema.tables` | table owner + comment (ownership / docs checks) |
| `{catalog}.information_schema.schemata` / `catalogs` | schema/catalog owner (inherited ownership) |
| `{catalog}.information_schema.table_tags` / `column_tags` | tags / PII |
| `{catalog}.information_schema.row_filters` | RLS |
| `{catalog}.information_schema.column_masks` | CLS |
| `system.information_schema.abac_policy_definitions` | ABAC (if available) |
| SCIM (`groups`, `service_principals`, `users` via SDK) | principal types, a user's groups |
| REST `/api/2.0/lineage-tracking/table-lineage`, `external-lineage` | real-time lineage (app SP) |

## Which identity runs what

| Call | Identity |
|------|----------|
| All SQL (pickers, lineage system table, grants, tags, columns) | the **app SP** (default, `query_identity: app`); the **viewer** when `query_identity: viewer` |
| Lineage REST API, SCIM | always the **app SP** |

With the default, viewers need no grants of their own — but everyone who can
open the app sees what the SP sees, so grant `CAN_USE` on the app only to the
dedicated admin group (and see v2 below). `query_identity: viewer` instead
makes every panel reflect what the signed-in user can see.

## Grants needed for the app service principal

```sql
GRANT USE CATALOG ON CATALOG system TO `ucviz-sp`;
GRANT USE SCHEMA  ON SCHEMA  system.access TO `ucviz-sp`;
GRANT SELECT ON TABLE system.access.table_lineage  TO `ucviz-sp`;
GRANT SELECT ON TABLE system.access.column_lineage TO `ucviz-sp`;
GRANT USE SCHEMA  ON SCHEMA  system.information_schema TO `ucviz-sp`;
-- metadata visibility on every catalog UCViz should cover:
GRANT BROWSE, USE CATALOG, USE SCHEMA ON CATALOG <catalog_name> TO `ucviz-sp`;
-- required to see other principals' grants (see PREREQUISITES.md §5):
GRANT MANAGE ON CATALOG <catalog_name> TO `ucviz-sp`;
```

Viewers need only `CAN_USE` on the app. Details: PREREQUISITES.md §5.

## Demo data (optional)

UCViz needs no demo data — it reads whatever is already in Unity Catalog. To
try it on a known lineage in a dev workspace, `scripts/seed_cross_catalog_demo.py`
builds a synthetic catalog-per-layer demo: `<prefix>_bronze/_silver/_gold`
catalogs (prefix `ucviz_demo` by default), schema `sales`, cross-catalog
CTAS/INSERT lineage, tags, a mask, and grants for two test groups,
`account users` and the app's service principal (looked up from the app name).

```bash
export UCVIZ_WH=<sql-warehouse-id>
python3 scripts/seed_cross_catalog_demo.py              # create
python3 scripts/seed_cross_catalog_demo.py --teardown   # remove
```

If the metastore can't take new catalogs, `UCVIZ_REUSE_CATALOGS=1` puts each
layer in its own schema inside existing catalogs (`UCVIZ_BRONZE_CAT`,
`UCVIZ_SILVER_CAT`, `UCVIZ_GOLD_CAT`) with grants scoped to those schemas. All
options are listed at the top of the script. `system.access.table_lineage` can
take up to ~1 hour to show the new edges.

## Configure & deploy

1. Bind a SQL warehouse resource named `sql-warehouse-id` to the app (the
   `app.yaml` reads `DATABRICKS_WAREHOUSE_ID` via `valueFrom: sql-warehouse-id`).
2. Grant the app's service principal the privileges above.
3. Deploy:
   ```bash
   databricks apps deploy ucviz \
     --source-code-path /Workspace/Users/<you>/UC_Visualizer
   ```
4. Open the **Admin** page (left nav) to select catalogs, run the connection
   test, and tune layer rules / tag roles.

## Notes & limitations (V1)

- **Config persistence:** the Admin panel writes `config/app_config.yaml`. If
  the deployed app's filesystem is read-only, edits are held in session state
  and offered as a YAML download to redeploy. (V2: store config in a UC volume
  or table so edits persist without redeploy.)
- **Admin gate:** access to the Admin page checks the signed-in user's direct
  SCIM groups against `admin_groups` and fails closed (denied) when groups
  can't be resolved. External Sources registration is off by default
  (`enable_external_source_writes: false`).
- **External Metadata / External Lineage** SDK surfaces are new; calls probe
  for available attribute names and degrade gracefully.
- Read-only: no grant/revoke or policy edits in V1 (see roadmap).

## Deployment notes — validate in your environment

- **`MANAGE` is required (verified).** Without it the SP only sees its own
  grants, so the Access / Catalog & Schema Grants / Principal Access views are
  nearly empty. Grant `MANAGE` at catalog level on each covered catalog.
- **App service principal grants.** The SP needs `USE CATALOG` on `system`,
  `USE SCHEMA` + `SELECT` on `system.access` (table lineage) and on
  `system.information_schema`, plus metadata visibility on every target
  catalog (`BROWSE`, or `USE CATALOG`/`USE SCHEMA`/`SELECT`) so the lineage API
  and information_schema return their objects. `system.access` grants must be
  made by an account/metastore admin. Viewers only need `CAN_USE` on the app
  (see "Which identity runs what"); full list in PREREQUISITES.md §5.
- **information_schema visibility.** It only returns objects visible to the
  caller, and only for catalogs **bound to the current workspace**. If the
  customer uses workspace-catalog bindings (e.g. bronze/silver/gold catalogs
  bound to different workspaces), those catalogs may need a read-only binding
  to the workspace hosting the app, or they won't appear in the pickers,
  grants or Principal Access views — even though `system.access.table_lineage`
  still records the lineage edges.
- **App access.** Grant `CAN_USE` on the app only to the dedicated group.
- **Group membership.** Principal Access folds in a user's *direct* SCIM
  groups (nested groups are not expanded). Reading them requires the app's
  service principal to be a **workspace admin** (INSTALL.md step 6).
  `account users` is opt-in — on a shared metastore it matches most catalogs.

## v2 — per-group catalog scoping (design only, not built)

Goal: each signed-in user only sees the catalogs/schemas mapped to their
Entra ID / account groups; everyone else gets "You don't have access".

- **Mapping table** (UC, owned by the UCViz admin group):
  `<admin_catalog>.ucviz.access_mapping(group_name STRING, catalog STRING,
  schema STRING)` — `schema` NULL means the whole catalog.
- **Hook:** `modules/access_scope.py` — `visible_catalogs()` /
  `visible_schemas()` are already called by the sidebar pickers and the
  Principal Access page (passthrough in v1). v2 resolves the viewer's groups
  (`principals.user_groups`), reads their mapping rows (cached per session) and
  intersects; an empty result renders `no_access_message()`.
- **Config:** `access_scope: {enabled: false, mapping_table: ...}`.
- Needed because of `query_identity: app` (the default): the SP sees every
  covered catalog, so the app has to do the per-group narrowing itself.

## Roadmap (V2+)

Grant/revoke from the app · column-level lineage drill-down · ABAC policy editor
· audit log viewer · cost attribution per lineage path · impact analysis
("if I revoke X, what breaks?") · drift detection · multi-workspace.
