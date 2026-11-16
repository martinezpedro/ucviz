# Installing UCViz

There are two ways to install. **Use the Quick install** unless you prefer the
command line. Both end in the same app. What the app does and how to use it is
in the in-app **Help → User Guide** (source: `docs/USER_GUIDE.md`).

## Quick install — from a notebook, no CLI (about 15 minutes)

**You need:** to be a **workspace admin**, and the names of the catalogs UCViz
should cover. Section 0 below explains every prerequisite in detail.

1. **Import the code.** In the workspace: **Workspace → Create → Git folder**
   → *Git repository URL* `https://github.com/martinezpedro/ucviz` → **Create
   Git folder**.
2. **Open the installer.** In the new folder open **`install/UCViz_Installer`**
   and attach **Serverless** compute (or any Unity Catalog compute).
3. **Fill in the boxes** at the top of the notebook (click **Run all** once if
   they don't show yet):
   - **Catalogs** — comma-separated, e.g. `bronze_cat, silver_cat, gold_cat`
   - **Admin group** — who can open UCViz's Admin page
   - **Users group** — who can use the app
   - **SQL warehouse** — name or ID (empty = first serverless warehouse)
   - **Create demo data** — `yes` only for a test workspace: builds a small
     synthetic bronze → silver → gold demo so you can see UCViz working
     before pointing it at real data (catalogs can then be left empty)
   - leave the rest as they are
4. **Click Run all.** It creates the app, binds the warehouse, writes the
   configuration, grants the app read access, makes its service principal a
   workspace admin, deploys, and sets who can use it. First run: 5–10 minutes.
5. **Read the summary** at the bottom: the app URL, what was done, and — if
   your account couldn't make some grants (usually the two `system.access`
   ones, which need an account or metastore admin) — the exact SQL to send to
   that person. Once they've run it, open the app.

**Check it works:** open the app → **Settings → Admin → Connection Test** —
every line should say **Granted**.

**Update later:** in the Git folder click **Pull** (top right), then open the
installer and **Run all** again. **Remove:** set *Action* to `uninstall` and
**Run all**.

Before running it, agree with your security team that the app's service
principal may hold `MANAGE` on the covered catalogs and be a workspace admin
(see 0.4). Either can be switched off in the installer boxes.

---

# Manual install — step by step (CLI)

This route does the same as the Quick install, by hand. Allow about 45
minutes, most of it waiting for the right people to run a few grants. If
something doesn't work, jump to [Troubleshooting](#troubleshooting) at the end.

---

## 0. Before you start — prerequisites

### 0.1 Platform

| Requirement | How to check |
|---|---|
| A Databricks workspace with **Unity Catalog** enabled | Catalog Explorer opens and shows catalogs. |
| **Databricks Apps** enabled on the workspace | The left menu shows **Compute → Apps** (or **+ New → App**). |
| A **SQL warehouse** (Serverless or Pro) UCViz can use | **SQL Warehouses** lists at least one warehouse. Note its **ID** (warehouse → *Connection details* → the last part of the HTTP path). |
| The **`system.access`** system schema enabled | An account admin can confirm under *Account console → System tables*, or run `SELECT 1 FROM system.access.table_lineage LIMIT 1`. |
| Lineage capture (on by default in Unity Catalog) | Catalog Explorer → any table → **Lineage** tab shows something. |

### 0.2 People you need

Some steps need elevated rights. Line these people up first:

| Who | Needed for |
|---|---|
| **Workspace admin** | Creating the app, adding its service principal to the workspace `admins` group (step 6), setting app permissions (step 8). |
| **Account admin or metastore admin** | Granting the app read access to `system.access` (step 5.1). |
| **Owner of each catalog** UCViz should cover (or a metastore admin) | Granting the app `BROWSE`/`USE`/`MANAGE` on those catalogs (step 5.2). |
| **Your security team** | Signing off that the app's service principal holds `MANAGE` on the covered catalogs and is a workspace admin (see 0.4). |

### 0.3 On your computer

| Tool | Version | Install |
|---|---|---|
| **Databricks CLI** | v0.230 or later (tested with v1.20) | `brew install databricks/tap/databricks` (macOS), or see the Databricks CLI install docs. Check with `databricks --version`. |
| **git** | any recent | `git --version` |

You don't need Python locally. The app installs its own dependencies when it
is deployed.

### 0.4 Decisions to make up front

1. **App name.** This guide uses `ucviz`. The app URL becomes
   `https://ucviz-<workspace-id>.<region>.azuredatabricksapps.com` (Azure) or
   similar on other clouds.
2. **Which catalogs UCViz should cover** (for example your bronze, silver and
   gold catalogs). UCViz only shows what you grant it.
3. **Who may use the app.** Pick or create one group (for example
   `ucviz-users`). Everyone in it sees who-has-access-to-what for the covered
   catalogs, so keep it to people who should see that.
4. **Who administers UCViz** (can open the Admin page). Pick a group, for
   example `ucviz-admins`.
5. **Security sign-off** on the app's service principal holding:
   - `MANAGE` on each covered catalog — needed to *see* other principals'
     grants. It also allows granting/revoking; UCViz never does either (it's
     read-only), but the identity could.
   - **Workspace admin** — needed to read users' group memberships (Principal
     Access → "Include access through group membership"). Without it the app
     still works but shows users' direct access only.

---

## 1. Get the code

```bash
git clone https://github.com/martinezpedro/ucviz.git
cd ucviz
```

(If you received the code as a zip or in your own repository, use that copy
instead.) The repository contains no workspace-specific settings — no
workspace URLs, IDs, catalogs or users. Everything specific to your
environment is set in steps 2–8.

The folder is the app. Everything under it is uploaded to the workspace in
step 7.

## 2. Configure the app

Edit `config/app_config.yaml`. At minimum set these two keys:

```yaml
# Catalogs UCViz may show. Leave empty only if the app should show every
# catalog its service principal can see.
catalogs:
  - <bronze_catalog>
  - <silver_catalog>
  - <gold_catalog>

# Groups allowed into the Admin page (workspace or account groups).
admin_groups:
  - ucviz-admins
```

Leave `query_identity: app` (the default). Everything else can stay as is and
can be tuned later in the Admin page — see "Configuration reference" at the end.

If your tables follow a naming convention that doesn't contain
`bronze`/`silver`/`gold`, also adjust `layer_rules` so the graph colors layers
correctly (the first rule whose text appears in `catalog.schema.table` wins).

## 3. Log the CLI in to your workspace

```bash
databricks auth login --host https://<your-workspace-url> --profile ucviz
```

A browser window opens; sign in. Check it worked:

```bash
databricks current-user me -p ucviz
```

All commands below use `-p ucviz`.

## 4. Create the app

### Option A — CLI (recommended)

Replace `<warehouse-id>` with the warehouse ID from 0.1:

```bash
databricks apps create --no-compute -p ucviz --json '{
  "name": "ucviz",
  "description": "Unity Catalog lineage and access visualizer (read-only)",
  "resources": [
    {
      "name": "sql-warehouse-id",
      "description": "SQL warehouse UCViz queries system tables through.",
      "sql_warehouse": { "id": "<warehouse-id>", "permission": "CAN_USE" }
    }
  ],
  "user_api_scopes": ["sql"]
}'
```

### Option B — UI

1. **Compute → Apps → Create app → Create a custom app**. Name: `ucviz`.
2. **Configure → App resources → Add resource → SQL warehouse**. Pick the
   warehouse, permission **Can use**, and set the resource key to exactly
   **`sql-warehouse-id`** (the app reads it by that name).
3. **User authorization → Add scope → `sql`**.
4. Create the app. Don't deploy yet.

### Get the app's service principal

Creating the app also creates a service principal for it. You need its
**application ID** for the grants:

```bash
databricks apps get ucviz -p ucviz -o json | grep service_principal
```

Note `service_principal_client_id` (a UUID) — referred to below as
`<app-sp>` — and `service_principal_name` (for example `app-xxxx ucviz`).

## 5. Grant the app read access

Run these in a SQL editor (or notebook) attached to any SQL warehouse. Always
quote the application ID with backticks.

### 5.1 System tables — account admin or metastore admin

```sql
GRANT USE CATALOG ON CATALOG system TO `<app-sp>`;
GRANT USE SCHEMA  ON SCHEMA  system.access TO `<app-sp>`;
GRANT SELECT ON TABLE system.access.table_lineage  TO `<app-sp>`;
GRANT SELECT ON TABLE system.access.column_lineage TO `<app-sp>`;
GRANT USE SCHEMA  ON SCHEMA  system.information_schema TO `<app-sp>`;
```

### 5.2 Each covered catalog — catalog owner or metastore admin

Repeat for every catalog listed in `catalogs` (step 2):

```sql
GRANT BROWSE, USE CATALOG, USE SCHEMA ON CATALOG <catalog> TO `<app-sp>`;
GRANT MANAGE ON CATALOG <catalog> TO `<app-sp>`;
```

- `BROWSE` lets the app read metadata (names, columns, tags, lineage) without
  reading data. UCViz never reads table contents.
- `MANAGE` is what lets the app see **other principals' grants**. Without it
  the Access tab, Catalog & Schema Grants and Principal Access show only the
  app's own grants and the owners. Grant it at **catalog** level — granting it
  on a schema hides the catalog-level grants.

### 5.3 Workspace-catalog bindings (only if you use them)

If a covered catalog is bound to specific workspaces, bind it (read-only is
enough) to the workspace that hosts the app: Catalog Explorer → catalog →
**Workspaces** tab. Unbound catalogs don't appear in UCViz.

## 6. Make the app's service principal a workspace admin

Needed so Principal Access can read users' group memberships (decision 0.4.5).
As a workspace admin:

1. **Settings → Identity and access → Groups → `admins`**.
2. **Add members** → search for the app's `service_principal_name`
   (from step 4) → **Add**.

CLI alternative (replace both IDs):

```bash
# admins group ID
databricks api get "/api/2.0/preview/scim/v2/Groups?filter=displayName%20eq%20%22admins%22&attributes=id" -p ucviz
# the SP's workspace SCIM ID (not the application ID)
databricks api get "/api/2.0/preview/scim/v2/ServicePrincipals?filter=applicationId%20eq%20%22<app-sp>%22&attributes=id" -p ucviz
databricks api patch "/api/2.0/preview/scim/v2/Groups/<admins-group-id>" -p ucviz --json '{
  "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
  "Operations": [{"op": "add", "value": {"members": [{"value": "<sp-scim-id>"}]}}]
}'
```

Skip this step if security doesn't approve; the app works without it (users'
group access just isn't folded in).

## 7. Upload and deploy

From the repo folder:

```bash
# upload the source (re-run after every change)
databricks sync . /Workspace/Users/<your-email>/ucviz -p ucviz --full

# deploy it
databricks apps deploy ucviz \
  --source-code-path /Workspace/Users/<your-email>/ucviz -p ucviz
```

The deploy takes 1–3 minutes the first time (it installs the Python packages
from `requirements.txt`). It should end with `"state": "SUCCEEDED"`. Get the
URL:

```bash
databricks apps get ucviz -p ucviz -o json | grep '"url"'
```

## 8. Give your users access

```bash
databricks apps update-permissions ucviz -p ucviz --json '{
  "access_control_list": [
    {"group_name": "ucviz-users",  "permission_level": "CAN_USE"},
    {"group_name": "ucviz-admins", "permission_level": "CAN_MANAGE"}
  ]
}'
```

Or in the UI: **Apps → ucviz → Permissions**. Users don't need any data
grants of their own — the app reads metadata as its service principal.

## 9. Verify (5 minutes)

1. Open the app URL. Approve the sign-in/consent prompt the first time.
2. **Settings → Admin** (as a member of an `admin_groups` group) →
   **Connection Test → Run connection test**. Every line must say **Granted**.
3. **Explore → Lineage & Access** → pick a Gold table in a covered catalog.
   The **Lineage** tab should trace back to Silver and Bronze, with a
   "Catalogs in this lineage" legend when the layers are in different catalogs.
4. **Access** tab: you should see grants for groups other than the app itself.
   If you only see owners and the app, `MANAGE` (step 5.2) is missing.
5. **Explore → Principal Access** → pick a user → with "Include access through
   group membership" ticked you should see "Including groups: …". If it says
   memberships can't be read, step 6 is missing.
6. **Help → User Guide** opens.

## 10. Updating, rolling back, removing

- **Update:** `git pull`, re-run both commands in step 7. Users keep their URL.
- **Config change:** edit `config/app_config.yaml` in the repo, then step 7.
  (Edits made in the Admin page only last until the next deploy.)
- **Roll back:** check out the previous commit and re-run step 7, or redeploy
  an earlier deployment from **Apps → ucviz → Deployments**.
- **Stop / remove:** `databricks apps stop ucviz -p ucviz` /
  `databricks apps delete ucviz -p ucviz`. Then revoke the grants from step 5
  and remove the service principal from `admins`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Deploy fails: "DATABRICKS_WAREHOUSE_ID is not set" | The warehouse resource isn't named `sql-warehouse-id` | Step 4 — rename the resource key. |
| App shows "App Not Available" | App is stopped or still starting | `databricks apps start ucviz -p ucviz`; check `databricks apps logs ucviz -p ucviz`. |
| Catalog dropdown is empty | No visibility, or `catalogs` lists names the app can't see | Step 5.2 grants; check spelling in `config/app_config.yaml`; step 5.3 bindings. |
| Connection Test: `system.access.*` not granted | Step 5.1 not done | Ask an account/metastore admin to run 5.1. |
| Lineage shows a warning that the system table couldn't be read | Same as above | Step 5.1. |
| Access tab only shows owners + the app | No `MANAGE` | Step 5.2 (`MANAGE` on the catalog). |
| Principal Access: "No group memberships could be read" | SP isn't a workspace admin | Step 6. |
| Admin page: "You are not a UCViz administrator" | Your group isn't in `admin_groups`, or the app lost its `sql` user scope | Step 2 (`admin_groups`), step 4 (user authorization `sql`), redeploy. |
| Sidebar shows plain file names instead of Explore / Settings / Help | A `pages/` folder exists in the uploaded source | Page files must live in `views/`; delete any `pages/` folder from the workspace copy and redeploy. |
| Changes in grants/tags don't show | Cache | Wait ~5 minutes and reload. |
| First load of a table takes 20–45 s | Normal on large metastores | Cached for 5 minutes afterwards. |

Logs: `databricks apps logs ucviz -p ucviz` (add `--tail-lines 200` for more).

---

## Configuration reference (`config/app_config.yaml`)

| Key | Default | Purpose |
|---|---|---|
| `catalogs` | `[]` | Catalogs UCViz may show. Empty = all the SP can see. |
| `admin_groups` | `account admins`, `metastore_admins`, `admins` | Groups allowed into the Admin page. |
| `query_identity` | `app` | `app` = the service principal runs all queries. `viewer` = queries run as the signed-in user (each user then needs their own grants). |
| `max_hops` | `5` | Default lineage depth (1–10 in the sidebar). |
| `max_graph_tables` | `50` | Above this, a whole schema/catalog pick must be narrowed first. |
| `layer_rules` | bronze/silver/gold/… | Name substring → layer, first match wins. |
| `tag_roles` | domain / sensitivity / abac keys | Which tag keys color as which role. |
| `high_sensitivity_values` | `pii, high, restricted, …` | Tag values shown in red. |
| `enable_external_source_writes` | `false` | Keep `false` — UCViz is read-only. |

## What gets installed

- One **Databricks App** (`ucviz`) and its auto-created **service principal**.
- No tables, jobs, pipelines or clusters. Queries run on the SQL warehouse you
  bound in step 4 and are billed as normal warehouse usage.
- Optional, for demos only: `scripts/seed_cross_catalog_demo.py` creates
  synthetic bronze/silver/gold demo tables — don't run it in production.
