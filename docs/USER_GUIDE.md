# UCViz User Guide

This guide is rendered inside the app (Help → User Guide) and lives in the repo
at `docs/USER_GUIDE.md`. Each `##` section below becomes a tab; each `###`
question under "Q&A" becomes an expandable entry.

## Overview

**UCViz (Unity Catalog Visualizer)** answers two questions about your data in
Unity Catalog:

1. **Where does this data come from?** Pick a table (for example a Gold data
   product) and UCViz traces its lineage back through Silver and Bronze to the
   original sources — even when each layer lives in a different catalog.
2. **Who can reach it?** For every table in that lineage, UCViz shows which
   users, groups and service principals have access, whether the access is
   granted directly or inherited from the schema or catalog, and who owns it.

It also works the other way round: pick a **principal** and see every catalog,
schema and table it can reach.

On top of that, it flags governance gaps per table — sensitive-looking columns
that aren't classified or masked, tables with no tags, no owner or no
description, and broad grants such as `account users`.

**What UCViz is**

- **Read-only.** It reads metadata only. It never reads table contents, and it
  never grants, revokes, alters or drops anything.
- **A view over what Unity Catalog already records** — lineage, grants, tags,
  masks, row filters and column names. It doesn't copy data or create tables.

**What UCViz is not**

- Not a place to change permissions. When it suggests a fix (for example a
  `REVOKE` or `ALTER TABLE ... SET TAGS`), it shows the SQL for you to review
  and run yourself through your normal change process.
- Not a data-quality or PII-detection engine. The sensitive-data check looks at
  **column names**, not values (see Limitations).

**Quick start (2 minutes)**

1. Open **Lineage & Access** (left navigation).
2. In the sidebar, pick a **Catalog**, a **Schema** and a **Table**. If you
   don't know which catalog the table is in, pick **★ All catalogs** and type
   part of the table name.
3. The **Lineage** tab draws the graph. Click a table to focus every other tab
   on it.
4. Open **Access** to see who can reach each table, and **Table Health** for
   governance findings.
5. To go from a person or group to their data, open **Principal Access**.

## How it works

### What's underneath

UCViz is a **Databricks App** (a small Python/Streamlit web app hosted inside
your Databricks workspace). It has no database of its own. Every screen is
built live from Unity Catalog metadata through a **SQL warehouse**, plus the
Unity Catalog **lineage API**.

```
Your browser ──► UCViz (Databricks App) ──► SQL warehouse ──► Unity Catalog system tables
                                         └─► Unity Catalog lineage API
```

### Which identity reads the metadata

- By default every query runs as the **app's service principal** (a dedicated
  identity created for the app). That's why you don't need personal grants on
  system tables to use UCViz — and why **everyone who can open the app sees the
  same results**: whatever the service principal can see.
- Access to the app itself is controlled by your administrators (who has
  *Can Use* on the app). Treat the app as a governance tool for the people who
  are allowed to see who-has-access-to-what.
- The **Admin** page checks *your own* group membership before it opens.

### Where each piece of information comes from

| What you see | Source |
|---|---|
| Lineage graph (Full trace) | `system.access.table_lineage` (last 90 days) merged with the real-time lineage API |
| Lineage graph (Step-by-step) | real-time lineage API |
| Who has access, owners | `information_schema` privilege views (`catalog_privileges`, `schema_privileges`, `table_privileges`) and owner columns of `catalogs`, `schemata`, `tables` |
| Tags, masks, row filters | `information_schema` `table_tags`, `column_tags`, `column_masks`, `row_filters` |
| Sensitive-data scan | column names from `information_schema.columns` |
| User / group / service principal labels | the workspace identity directory (SCIM) |

### How the lineage trace works

- **Full trace (cross-catalog)** starts at the table you picked and walks one
  hop at a time — upstream (where the data came from) and, if selected,
  downstream (what is built from it). Each hop looks up every table on the
  current frontier, across **all catalogs**, until there are no more parents,
  the **Max Lineage Hops** limit is reached, or the graph hits 300 tables. A
  table is never expanded twice, so circular lineage can't loop forever.
- **Step-by-step (real-time API)** shows the table and its direct neighbours.
  Nodes marked **＋** have more lineage; click one to reveal its next hop.

### How fresh is it

- Lineage written in the **last 90 days** is included. Lineage in
  `system.access.table_lineage` can take **up to about an hour** to appear
  after a table is written; the real-time API (used by both modes) usually
  shows it within minutes.
- Grants, tags and owners are read live, then **cached for 5 minutes**. The
  list of catalogs and schemas is cached for 10 minutes, and the identity
  directory for 1 hour. If you just changed a grant, wait a few minutes or
  reload later.

## Using the app

### Navigation

- **Explore → Lineage & Access** — start from a table.
- **Explore → Principal Access** — start from a user, group or service principal.
- **Settings → Admin** — configuration (UCViz administrators only).
- **Help → User Guide** — this guide.

### Lineage & Access — sidebar controls

| Control | What it does |
|---|---|
| **Catalog** | Pick a catalog, or **★ All catalogs** to search for a table anywhere. Switching to ★ All catalogs keeps the table you had open. |
| **Schema** | Pick a schema, or **★ All schemas** to work across the whole catalog. A new catalog starts on its first schema that has tables (empty ones such as `default` are skipped). |
| **Table** | Pick one table, or **★ All tables in this schema / catalog** to graph the whole scope (a whole scope always traces upstream only). With ★ All catalogs, type part of a name in **Find a table**, then pick a match (up to 200 matches are listed). |
| **Lineage** | **Full trace (cross-catalog)** or **Step-by-step (real-time API)**. |
| **Direction** (Full trace only) | **Upstream + downstream** or **Upstream only**. |
| **Max Lineage Hops** | How many steps away from the table to trace (1–10, default 5). |
| **Filter by Layer** | Show only Gold, Silver + Gold, etc. in the Access, Tags and Sensitive Data tabs. |
| **Filter by Group** | Show only rows for one principal in the Access tab. |
| **Multi-select nodes** | When on, each click adds a table to the selection instead of replacing it. |

**Big scopes.** If "All tables" covers more than 50 tables, the graph isn't
drawn straight away. A **Narrow Scope** panel lets you filter by layer, tag
value, group with access, or name (filters combine). You can also tick
**Graph the first 50 anyway**.

**Your view is in the URL.** Catalog, schema, table and the selected node are
kept in the address bar, so a refresh restores the view and you can share the
link with a colleague who has access to the app.

### Lineage & Access — tabs

- **Lineage** — the graph. Fill color = medallion layer; the colored stripe on
  the left of each node = its catalog (see the "Catalogs in this lineage"
  legend). Click a node to select it: it gets a highlighted border, its edges
  animate, and every other tab narrows to it. **Clear** (top right) removes the
  selection. Below the graph, **Lineage Chain** lists every hop as text.
- **Access** — one row per principal per table: **Principal** (with a
  user / group / service principal label), **Access Via** (Direct, inherited
  from Schema or Catalog, or Owner), **Privilege** and **Status**.
- **Catalog & Schema Grants** — grants made on the catalog and schema
  themselves (plus their owners), independent of the graph. It's shown even
  when no table is picked, so you can review a schema before looking at
  tables. **Include inherited** also shows catalog grants as they apply to each
  schema; **Filter principal** narrows by name.
- **Table Health** — governance findings for the tables in view, most severe
  first, each with a suggested fix (see "Reading the results").
- **Tags** — table tags, column tags, row-level security (RLS) and column
  masks (CLS) per table.
- **Sensitive Data** — columns whose **names** look like personal or sensitive
  data, whether they're already classified or masked, and a suggested fix.

The cards at the top summarise the whole scope: hops, tables in the chain,
principals with access, health warnings, distinct tags, tables with RLS,
masked columns, and unclassified sensitive columns.

### Principal Access

The first visit takes a few seconds while UCViz loads the workspace's users,
groups and service principals ("Loading users, groups and service
principals…"); after that it's cached for 10 minutes.

1. Choose a **Type** (All, Groups, Users, Service principals) and pick a
   **Principal**, or type one in **…or type one** (a user's email, a group
   name, or a service principal's application ID).
2. For a user, **Include access through group membership** adds what the
   user's groups were granted. **Include `account users`** adds the group every
   user belongs to — it's off by default because it usually matches a lot.
3. The top table lists every **catalog** with access, the privileges on the
   catalog itself, and **Via** (direct, or which group).
4. Use **Drill into catalog** → **Drill into schema** to go down to tables.
   **Show inherited table access** switches between all table access and only
   direct grants or ownership.

### Admin (UCViz administrators only)

- **Catalog Access** — which catalogs UCViz may scan, default hops, and the
  table limit before narrowing is required. Changes here last until the next
  deploy; to change the catalog list for good, re-run the installer (see "For
  administrators").
- **Connection Test** — checks that the app can read every system table it
  needs. Run this first when something looks empty.
- **Layer Mapping** — rules that decide which layer a table belongs to (see
  "Reading the results").
- **Tag Definitions** — which tag keys mean domain, sensitivity or ABAC, and
  which values show in red.
- **External Sources** — lists external systems registered in Unity Catalog.
  Registering new ones is disabled because UCViz is read-only.

## Reading the results

### Layers (node color)

Layers come from **Layer Mapping** rules: the first rule whose text appears in
the table's full name (`catalog.schema.table`) wins. With the default rules, a
name containing `bronze`, `raw` or `landing` is Bronze; `silver` or `curated`
is Silver; `gold` or `semantic` is Gold; `mart` or `consumer` is Consumer;
`external` or `source` is External. Names that match nothing are **Unknown**
(grey). If your naming differs, ask your UCViz administrator to adjust the
rules.

### Access Via

| Value | Meaning |
|---|---|
| **Direct** | Granted on this exact object. |
| **Schema** | Inherited from a grant on the schema. |
| **Catalog** | Inherited from a grant on the catalog. |
| **Metastore** | Inherited from the metastore. |
| **Owner** | The principal owns the table, its schema or its catalog. Owners have full access but don't appear as grants in Unity Catalog. |

### Status

- **Granted** — normal access.
- **Unexpected** — a broad principal (`account users`, `users`, `public`) has
  more than a USE privilege. Usually means "everyone can read this".
- **No Grants** — nobody has access and there is no owner recorded.

### Table Health checks

| Check | Severity | What it means |
|---|---|---|
| Unclassified sensitive data | High | A column name looks sensitive but has no classification tag or mask. |
| Sensitive column not masked | High | The column is classified as sensitive but has no column mask. |
| Over-permissioned | High | A broad principal has access. |
| No tags / classification | Medium | The table has no tags at all. |
| No owner set | Medium | No accountable owner. |
| No effective access | Medium | No grants and no owner — unreachable. |
| Missing documentation | Low | The table has no description. |

Each finding comes with a suggested SQL statement. **Review it before running
it** — tag names and values must follow your organisation's tag policies.

### Sensitive-data categories

The scan matches column names against these categories: Credential/Secret,
Government ID, Financial, Health (PHI), Email, Phone, Date of Birth, Person
Name, Online Identifier, Postal Address, Geolocation, Demographic. A column is
**Masked** if a column mask is applied, **Classified** if it has a sensitivity
tag, otherwise **Unclassified — review**.

## Limitations

- **Read-only by design.** UCViz suggests fixes; it never applies them.
- **Lineage only covers what Unity Catalog recorded** — writes through
  Databricks (SQL, notebooks, jobs, pipelines) in the last 90 days. Data moved
  by tools outside Databricks, or by file copies that Unity Catalog can't see,
  won't show unless it's registered as external lineage. Views and tables
  created long ago and never rewritten may show no lineage.
- **New lineage can lag** up to about an hour in the system table.
- **Visibility follows the app's identity.** UCViz only shows catalogs,
  schemas, tables and grants the app's service principal can see, and only
  catalogs bound to the workspace where the app runs.
- **Grant visibility needs MANAGE.** If the service principal doesn't hold
  MANAGE (or ownership) on a catalog, its Access and grants views only show the
  app's own grants and the owners.
- **Group membership.** Principal Access can only fold in a user's groups if
  the app can read group membership from the identity directory; otherwise it
  says so and shows the user's direct access. Groups inside groups (nested
  groups) are not expanded.
- **Sensitive-data detection is name-based.** It flags `email` or `ssn` columns
  by name; it can't find sensitive data hidden in a column called `notes`, and
  it may flag a column that only looks sensitive. Treat results as a review
  list.
- **Size limits.** Graphs stop at 300 tables; whole-scope graphs ask you to
  narrow above 50 tables; tables show up to 500 rows; Principal Access reads up
  to 5,000 rows per view (a warning appears if that's hit).
- **Speed.** A first trace across catalogs can take 20–45 seconds on a large
  metastore; repeating it within 5 minutes is near-instant. Principal Access for
  a principal with many grants can take up to about 40 seconds.
- **Changes appear after the cache expires** (about 5 minutes for grants and
  tags).
- **Admin changes** are saved to the running app's copy of the configuration;
  redeploying the app restores the version in the code repository. Ask your
  administrator to keep the repository copy up to date.

## Q&A

### Does UCViz change anything in Unity Catalog?

No. It only reads metadata. Suggested SQL (GRANT, REVOKE, SET TAGS, SET MASK)
is shown as text for you to review and run yourself.

### Does UCViz read the data inside my tables?

No. It reads metadata only: names, lineage, grants, tags, masks and column
names and types. The sensitive-data scan looks at column names, never values.

### Why don't I see a catalog, schema or table I know exists?

The most common reasons:

1. The app's service principal has no visibility on it (it needs at least
   BROWSE, or USE CATALOG / USE SCHEMA, on the catalog).
2. It isn't in the catalogs UCViz was installed for. Your administrator adds
   it by re-running the installer with the catalog in the **Catalogs** box.
3. The catalog isn't bound to the workspace where UCViz runs.
4. It was created in the last few minutes — the catalog and schema lists
   refresh every 10 minutes.

### Why is the lineage graph just one node?

Unity Catalog has no recorded lineage for that table in the last 90 days.
Typical causes: the table was loaded by a tool outside Databricks, created long
ago and never rewritten, or written in the last hour (try **Step-by-step**,
which uses the real-time API). Also check **Max Lineage Hops** isn't set to 1
and that **Upstream only** isn't hiding consumers you expected.

### Why does a Bronze schema show tables with no arrows?

With **★ All tables in this schema** picked, UCViz traces each table
**upstream only**, and Bronze tables usually have nothing upstream (they're
loaded from files or outside sources). Pick a single Bronze table instead and
keep **Upstream + downstream** on to see the Silver and Gold tables built from
it.

### The Lineage page says "No tables in schema …".

The selected schema is empty (or the app can't see its tables). Pick another
schema in the sidebar. Every new catalog has an empty `default` schema.

### Why does my Gold table stop at Silver instead of going back to Bronze?

Either the Silver table has no recorded upstream lineage, or **Max Lineage
Hops** is lower than the number of steps. Increase the hops. In Step-by-step
mode, click the Silver node (marked ＋) to reveal its next hop.

### Why does a table appear in a different layer color than I expect?

Layers come from name rules (Admin → Layer Mapping). A table called
`sales_summary` in a catalog called `analytics` matches no default rule and
shows as Unknown. Ask your UCViz administrator to add a rule.

### Why does the Access tab only show owners and the app itself?

The app's service principal needs **MANAGE** (or ownership) on the catalog to
see other principals' grants — Unity Catalog hides grants from identities that
can't manage the object. Ask your administrator to grant MANAGE on the catalog
to the app.

### What's the difference between Direct, Schema and Catalog under "Access Via"?

Direct means the grant is on the table itself. Schema or Catalog means the
principal received the privilege on the parent schema or catalog, and the table
inherits it. To remove inherited access you change the grant on the parent, not
on the table.

### A user has access but they aren't listed in the Access tab. Why?

They probably get it through a group. The Access tab lists grantees exactly as
granted (groups appear as groups). Use **Principal Access** with the user and
**Include access through group membership** to see access via their groups.

### Why does Principal Access say it couldn't read group memberships?

Reading which groups a user belongs to requires extra directory rights for the
app. Without them, Principal Access shows only the user's direct grants. Your
administrator can decide whether to grant those rights.

### Why are some principals shown as long IDs?

Service principals are granted by application ID. UCViz shows the display name
where the directory provides one; otherwise you see the ID.

### What does "Unexpected" mean in the Status column?

A broad principal — `account users`, `users` or `public` — holds more than a
USE privilege on that table, which usually means everyone in the account can
read it. Check whether that's intended.

### Is a "Sensitive Data" finding confirmed PII?

No. It means the column **name** looks like a sensitive category. Review it,
then classify and mask it if it really is sensitive.

### I fixed a grant or added a tag but UCViz still shows the old state.

Results are cached for about 5 minutes. Reload the page after that.

### Why is the first load of a table slow?

UCViz queries lineage hop by hop across catalogs, then reads grants, tags and
columns for every table in the chain. On a large metastore that takes
20–45 seconds the first time; it's cached for 5 minutes afterwards.

### The page says the system lineage table "could not be read".

The app's service principal can't read `system.access.table_lineage`. The graph
still shows real-time lineage for tables the app can see, but cross-catalog
history may be incomplete. Your administrator can run **Admin → Connection
Test** to confirm and grant access. On **Databricks Free Edition** this grant
can't be made (no metastore admin), so this warning is expected there.

### Can I share what I'm looking at?

Yes. Copy the browser URL — it includes the catalog, schema, table and
selected node. The other person needs access to the app.

### Why can't I open the Admin page?

Admin opens only for members of the groups listed in the app's configuration
(`admin_groups`). Ask an existing UCViz administrator to add you or your group.

### How do I add or remove a catalog?

Open the `install/UCViz_Installer` notebook in the UCViz Git folder, change the
**Catalogs** box (`all`, or a comma-separated list such as
`sales_gold, sales_silver, finance_gold`) and click **Run all**. The same app is
updated and the URL doesn't change. Removing a catalog hides it from UCViz but
doesn't revoke the grants already given to the app; the catalog owner can
revoke them (see "For administrators").

### How do I give more people access to UCViz?

Either add them under **Compute → Apps → ucviz → Permissions** (**Can Use**),
or put the group or emails in the installer's **Users** box and click
**Run all**. The installer replaces the app's permission list with what's in
the box, so keep anyone who already has access in it (comma-separated). A group is easier to maintain than a list of people. Everyone with
access sees who has access to what in the covered catalogs, so keep it to
people who should see that. Users don't need any data grants of their own.

### How do I update UCViz to a newer version?

In the UCViz Git folder click the branch name (`main`) → **Pull**, then open
`install/UCViz_Installer` and click **Run all**. No re-import is needed and the
URL doesn't change.

### Can I try UCViz without pointing it at real data?

Yes. In the installer set **Create demo data** to `yes`. It builds a small
synthetic bronze → silver → gold demo (catalogs `ucviz_demo_bronze`,
`ucviz_demo_silver`, `ucviz_demo_gold`, or three schemas if the workspace
can't create catalogs) with lineage, tags and a mask. Then open
`ucviz_demo_gold` → `sales` → `customer_360`. To remove it, run the installer
with **Action** = `uninstall` and **Create demo data** = `yes`.

### Who do I ask if something is still wrong?

Start with your UCViz administrator — they can run **Admin → Connection
Test**, which shows exactly which system table or catalog the app can't read.
The "For administrators" tab lists what each check needs.

## For administrators

### Install, update or remove

UCViz is installed from a notebook, no CLI needed (full steps in `INSTALL.md`
in the repository):

1. **Workspace → Create → Git folder** → `https://github.com/martinezpedro/ucviz`.
2. Open **`install/UCViz_Installer`**, attach **Serverless** compute.
3. Fill in the boxes at the top (click **Run all** once if they don't show):

| Box | What to enter |
|---|---|
| Catalogs | `all` (default) = every catalog you can see, or a comma-separated list. In production a list is usually better: the app gets read access, and MANAGE if enabled, on each covered catalog. |
| Admin group | Optional. Group allowed into the Admin page; it also gets Can Manage on the app. Empty = workspace `admins`, `metastore_admins` and `account admins` only. |
| Users | Who may use the app (Can Use): a group, or comma-separated user emails. |
| SQL warehouse | Name or ID; empty = a running serverless warehouse. |
| App name | Default `ucviz`. |
| Grant MANAGE | `yes` so the app can see everyone's grants (needs security sign-off). |
| Make app SP workspace admin | `yes` so Principal Access can read group memberships. |
| Action | `install` (also updates) or `uninstall`. |
| Create demo data | `yes` only on a test workspace. |

4. Click **Run all**. The summary at the bottom shows the app URL, what was
   done, and the exact SQL for any grant your account couldn't make (usually
   the `system.access` ones, which need an account or metastore admin).

**Update:** Git folder → **Pull**, then **Run all** again. **Change catalogs:**
edit the Catalogs box, **Run all**. **Remove:** Action = `uninstall`, **Run all**.
Removing a catalog from the list doesn't revoke the app's grants on it; to do
that, the catalog owner runs:

```sql
REVOKE MANAGE ON CATALOG <catalog> FROM `<app-sp-application-id>`;
REVOKE BROWSE, USE CATALOG, USE SCHEMA ON CATALOG <catalog> FROM `<app-sp-application-id>`;
```

### What the app needs

Grants for the app's service principal (use its application ID):

```sql
GRANT USE CATALOG ON CATALOG system TO `<app-sp-application-id>`;
GRANT USE SCHEMA  ON SCHEMA  system.access TO `<app-sp-application-id>`;
GRANT SELECT ON TABLE system.access.table_lineage  TO `<app-sp-application-id>`;
GRANT SELECT ON TABLE system.access.column_lineage TO `<app-sp-application-id>`;
GRANT USE SCHEMA  ON SCHEMA  system.information_schema TO `<app-sp-application-id>`;
-- for every catalog UCViz should cover:
GRANT BROWSE, USE CATALOG, USE SCHEMA ON CATALOG <catalog> TO `<app-sp-application-id>`;
GRANT MANAGE ON CATALOG <catalog> TO `<app-sp-application-id>`;
```

- `system.access` grants must be made by an account or metastore admin.
- **MANAGE** lets the app see everyone's grants. It also allows granting and
  revoking; UCViz never does either, but restrict who can modify or redeploy
  the app.
- Bind a SQL warehouse to the app as the resource `sql-warehouse-id`.
- Keep the app's **user authorization** with the `sql` scope enabled — the
  Admin page uses it to check the signed-in user's groups.
- Grant **Can Use** on the app only to the people who should see
  who-has-access-to-what.
- Catalogs bound to other workspaces need a binding to the app's workspace to
  appear.

### Configuration (`config/app_config.yaml`)

| Key | Purpose |
|---|---|
| `catalogs` | Catalogs UCViz may scan. Empty = every catalog the app can see (what the installer writes for `all`). |
| `admin_groups` | Groups allowed into the Admin page. |
| `query_identity` | `app` (default: the service principal runs all queries) or `viewer` (queries run as the signed-in user). |
| `max_hops` | Default lineage depth. |
| `max_graph_tables` | Table count above which a whole-scope pick must be narrowed. |
| `layer_rules` | Name substring → layer. |
| `tag_roles`, `high_sensitivity_values` | Tag key roles and red values. |
| `enable_external_source_writes` | Keep `false` for a read-only install. |

### Troubleshooting checklist

1. **Admin → Connection Test** — every line should be Granted.
2. Empty pickers → check the `catalogs` list, the app's BROWSE/USE grants, and
   workspace-catalog bindings.
3. Access tab shows only owners → grant MANAGE on the catalog.
4. "System lineage table could not be read" → grant SELECT on
   `system.access.table_lineage`.
5. Admin page refuses an admin → check `admin_groups` and that user
   authorization (`sql` scope) is enabled on the app.

## Glossary

| Term | Meaning |
|---|---|
| **Unity Catalog (UC)** | Databricks' governance layer: catalogs, schemas, tables, permissions, lineage, tags. |
| **Catalog / Schema / Table** | The three-level name of every table: `catalog.schema.table`. |
| **Medallion layers** | Bronze (raw), Silver (cleaned), Gold (business-ready data products). |
| **Lineage** | Which tables a table was built from (upstream) and which are built from it (downstream). |
| **Hop** | One step in lineage, from a table to a direct parent or child. |
| **Principal** | Anyone who can be granted access: a user, a group, or a service principal. |
| **Service principal** | A non-human identity used by applications and jobs. |
| **Grant / Privilege** | A permission such as SELECT, USE CATALOG, USE SCHEMA, MODIFY, MANAGE. |
| **Owner** | The principal that owns an object and has full control of it. |
| **Inherited access** | A privilege granted on a catalog or schema that applies to everything inside it. |
| **Tag** | A key/value label on a table or column, e.g. `classification = restricted`. |
| **Column mask (CLS)** | A function that hides or redacts a column's values for some users. |
| **Row filter (RLS)** | A function that hides rows from some users. |
| **System tables** | Read-only tables under the `system` catalog where Databricks records lineage, audit and usage. |
| **information_schema** | Standard views describing objects, grants and tags in each catalog. |
